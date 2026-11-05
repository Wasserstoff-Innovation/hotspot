/**
 * Hotspot web script
 *
 * One script tag records page views, clicks (with rage click and dead click detection), form
 * submits, scroll depth, time on page, JavaScript errors and single-page-app route changes, and
 * sends them in batches to your own Hotspot server.
 *
 *   <script src="https://your-hotspot-host/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>
 *
 * Optional attributes: data-api-host (defaults to the host the script was loaded from),
 * data-autocapture="false", data-capture-errors="false", data-capture-pageview="false",
 * data-debug="true" (console logging), data-debug-hud="true" (on-page click overlay, Alt+H).
 *
 * The visitor id and session id live in localStorage (cookie fallback). No third-party cookies.
 */
;(function (window, document) {
    'use strict'

    if (!window || !document || window.__hotspot_loaded) {
        return
    }

    // Pages framed by the dashboard replayer must not record a second time.
    try {
        if (window.location.search.indexOf('hotspot_replay=1') !== -1 || window.self !== window.top) {
            return
        }
    } catch (e) {
        return
    }

    window.__hotspot_loaded = true

    var VERSION = '3.0.0'
    var STORAGE_PREFIX = '__hotspot_'
    var DISTINCT_ID_KEY = STORAGE_PREFIX + 'distinct_id'
    var SESSION_ID_KEY = STORAGE_PREFIX + 'session_id'
    var SESSION_TIMESTAMP_KEY = STORAGE_PREFIX + 'session_ts'
    var DEVICE_ID_KEY = STORAGE_PREFIX + 'device_id'
    var OPT_OUT_KEY = STORAGE_PREFIX + 'opt_out'
    var SUPER_PROPS_KEY = STORAGE_PREFIX + 'super_props'
    var HUD_CLICKS_KEY = STORAGE_PREFIX + 'hud_clicks'
    var MAX_RETRY_QUEUE = 50
    var MAX_STACK_LENGTH = 2000

    var config = {
        api_key: '',
        api_host: '',
        autocapture: true,
        capture_pageview: true,
        capture_pageleave: true,
        capture_errors: true,
        session_timeout_ms: 30 * 60 * 1000,
        batch_size: 10,
        batch_interval_ms: 2500,
        rage_click_window_ms: 1200,
        rage_click_radius_px: 40,
        rage_click_count: 3,
        debug: false,
        debug_hud: false,
        persistence: 'localStorage',
        sanitize_inputs: true,
    }

    // ----- Script tag configuration -------------------------------------------------------------

    var currentScript =
        document.currentScript ||
        (function () {
            var scripts = document.getElementsByTagName('script')
            for (var i = scripts.length - 1; i >= 0; i--) {
                if (scripts[i].src && scripts[i].src.indexOf('hotspot.js') !== -1) {
                    return scripts[i]
                }
            }
            return null
        })()

    function attribute(name) {
        return currentScript ? currentScript.getAttribute(name) : null
    }

    function isFalse(value) {
        return value === 'false' || value === '0'
    }

    if (currentScript) {
        var key = attribute('data-api-key') || attribute('data-key')
        if (key) config.api_key = key
        var host = attribute('data-api-host')
        if (host) config.api_host = host.replace(/\/+$/, '')
        if (isFalse(attribute('data-autocapture'))) config.autocapture = false
        if (isFalse(attribute('data-capture-errors'))) config.capture_errors = false
        if (isFalse(attribute('data-capture-pageview'))) config.capture_pageview = false
        if (attribute('data-debug') === 'true') config.debug = true
        if (attribute('data-debug-hud') === 'true') config.debug_hud = true
        if (!config.api_host && currentScript.src) {
            try {
                config.api_host = new URL(currentScript.src).origin
            } catch (e) {
                config.api_host = window.location.origin
            }
        }
    }
    if (!config.api_host) {
        config.api_host = window.location.origin
    }
    if (window.location.search.indexOf('hotspot_debug_hud=1') !== -1) {
        config.debug_hud = true
    }

    // ----- Helpers ------------------------------------------------------------------------------

    function log() {
        if (config.debug && window.console && window.console.log) {
            var args = Array.prototype.slice.call(arguments)
            args.unshift('[Hotspot]')
            window.console.log.apply(window.console, args)
        }
    }

    function uuidv4() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID()
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = (Math.random() * 16) | 0
            var v = c === 'x' ? r : (r & 0x3) | 0x8
            return v.toString(16)
        })
    }

    var storage = {
        get: function (name) {
            try {
                if (config.persistence === 'localStorage' && window.localStorage) {
                    return window.localStorage.getItem(name)
                }
            } catch (e) {}
            var prefix = name + '='
            var parts = document.cookie.split(';')
            for (var i = 0; i < parts.length; i++) {
                var part = parts[i].trim()
                if (part.indexOf(prefix) === 0) return decodeURIComponent(part.substring(prefix.length))
            }
            return null
        },
        set: function (name, value) {
            try {
                if (config.persistence === 'localStorage' && window.localStorage) {
                    window.localStorage.setItem(name, value)
                    return
                }
            } catch (e) {}
            document.cookie = name + '=' + encodeURIComponent(value) + '; max-age=31536000; path=/; SameSite=Lax'
        },
        remove: function (name) {
            try {
                if (window.localStorage) window.localStorage.removeItem(name)
            } catch (e) {}
            document.cookie = name + '=; max-age=0; path=/'
        },
    }

    function persistentId(name) {
        var id = storage.get(name)
        if (!id) {
            id = uuidv4()
            storage.set(name, id)
        }
        return id
    }

    function getDistinctId() {
        return persistentId(DISTINCT_ID_KEY)
    }

    function getSessionId() {
        var now = Date.now()
        var lastSeen = parseInt(storage.get(SESSION_TIMESTAMP_KEY) || '0', 10)
        var sessionId = storage.get(SESSION_ID_KEY)
        if (!sessionId || now - lastSeen > config.session_timeout_ms) {
            sessionId = uuidv4()
            storage.set(SESSION_ID_KEY, sessionId)
        }
        storage.set(SESSION_TIMESTAMP_KEY, String(now))
        return sessionId
    }

    function getSuperProperties() {
        try {
            var raw = storage.get(SUPER_PROPS_KEY)
            return raw ? JSON.parse(raw) : {}
        } catch (e) {
            return {}
        }
    }

    function setSuperProperties(props) {
        var current = getSuperProperties()
        for (var name in props) {
            if (Object.prototype.hasOwnProperty.call(props, name)) current[name] = props[name]
        }
        storage.set(SUPER_PROPS_KEY, JSON.stringify(current))
    }

    function utmProperties() {
        var props = {}
        if (!window.location.search) return props
        try {
            var params = new URLSearchParams(window.location.search)
            var keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'ref']
            for (var i = 0; i < keys.length; i++) {
                var value = params.get(keys[i])
                if (value) props[keys[i]] = value
            }
        } catch (e) {}
        return props
    }

    var AI_REFERRERS = [
        ['perplexity.ai', 'Perplexity'],
        ['chatgpt.com', 'ChatGPT'],
        ['openai.com', 'ChatGPT'],
        ['claude.ai', 'Claude'],
        ['gemini.google.com', 'Gemini'],
        ['copilot.microsoft.com', 'Copilot'],
    ]

    function referrerProperties() {
        var props = { $referrer: document.referrer || '', $referrer_host: '', $is_ai_referral: false, $ai_source: '' }
        if (!document.referrer) return props
        try {
            var hostname = new URL(document.referrer).hostname
            props.$referrer_host = hostname
            for (var i = 0; i < AI_REFERRERS.length; i++) {
                if (hostname.indexOf(AI_REFERRERS[i][0]) !== -1) {
                    props.$is_ai_referral = true
                    props.$ai_source = AI_REFERRERS[i][1]
                    break
                }
            }
        } catch (e) {}
        return props
    }

    function baseProperties() {
        var width = window.innerWidth || document.documentElement.clientWidth || 0
        var height = window.innerHeight || document.documentElement.clientHeight || 0
        var props = {
            $lib: 'hotspot-web',
            $lib_version: VERSION,
            $screen_width: window.screen ? window.screen.width : width,
            $screen_height: window.screen ? window.screen.height : height,
            $viewport_width: width,
            $viewport_height: height,
            $current_url: window.location.href,
            $host: window.location.host,
            $pathname: window.location.pathname,
            $title: document.title || '',
            $device_id: persistentId(DEVICE_ID_KEY),
            $session_id: getSessionId(),
            $browser_language: navigator.language || '',
            $timezone: '',
        }
        try {
            props.$timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
        } catch (e) {}
        return props
    }

    function merge() {
        var result = {}
        for (var i = 0; i < arguments.length; i++) {
            var source = arguments[i]
            if (!source) continue
            for (var name in source) {
                if (Object.prototype.hasOwnProperty.call(source, name)) result[name] = source[name]
            }
        }
        return result
    }

    // ----- Queue and transport ------------------------------------------------------------------

    var queue = []
    var flushTimer = null
    var listeners = {}

    function endpoint() {
        return config.api_host + '/hotspot/batch/'
    }

    function send(payload, sync) {
        if (sync && navigator.sendBeacon) {
            // text/plain keeps the beacon a simple request, so the browser sends it without a preflight.
            var blob = new Blob([payload], { type: 'text/plain;charset=UTF-8' })
            if (navigator.sendBeacon(endpoint(), blob)) return true
        }
        if (!window.fetch) return false
        return window.fetch(endpoint(), {
            method: 'POST',
            body: payload,
            headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
            mode: 'cors',
            keepalive: !!sync,
        })
    }

    function flush(sync) {
        if (!queue.length) return
        if (!config.api_key) {
            log('No api key configured, dropping ' + queue.length + ' event(s)')
            queue = []
            return
        }
        var batch = queue.slice()
        queue = []
        var payload = JSON.stringify({ api_key: config.api_key, batch: batch, sent_at: new Date().toISOString() })
        log('Sending ' + batch.length + ' event(s)')
        var result
        try {
            result = send(payload, sync)
        } catch (e) {
            result = false
        }
        if (result && typeof result.then === 'function') {
            result
                .then(function (response) {
                    if (!response.ok) log('Server answered ' + response.status)
                })
                .catch(function (error) {
                    log('Delivery failed, keeping events for retry', error)
                    if (queue.length < MAX_RETRY_QUEUE) queue = batch.concat(queue)
                })
        }
    }

    function scheduleFlush() {
        if (flushTimer) clearTimeout(flushTimer)
        if (queue.length >= config.batch_size) {
            flush(false)
            return
        }
        flushTimer = setTimeout(function () {
            flush(false)
        }, config.batch_interval_ms)
    }

    function record(eventName, properties) {
        if (storage.get(OPT_OUT_KEY) === 'true') return
        var eventObject = {
            event: eventName,
            timestamp: new Date().toISOString(),
            properties: merge(baseProperties(), referrerProperties(), utmProperties(), getSuperProperties(), properties, {
                distinct_id: getDistinctId(),
            }),
        }
        queue.push(eventObject)
        log('Captured ' + eventName, eventObject)
        var callbacks = listeners[eventName] || []
        for (var i = 0; i < callbacks.length; i++) {
            try {
                callbacks[i](eventObject)
            } catch (e) {}
        }
        scheduleFlush()
    }

    // ----- Autocapture --------------------------------------------------------------------------

    function cssSelector(element) {
        if (!element || element.nodeType !== 1) return ''
        if (element.id) return '#' + element.id
        var selector = element.tagName.toLowerCase()
        if (typeof element.className === 'string' && element.className.trim()) {
            var classes = element.className
                .trim()
                .split(/\s+/)
                .filter(function (name) {
                    return name && name.indexOf(':') === -1
                })
                .slice(0, 3)
            if (classes.length) selector += '.' + classes.join('.')
        }
        return selector
    }

    function elementText(element) {
        if (!element) return ''
        if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') {
            return ''
        }
        return (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 80)
    }

    var INTERACTIVE_TAGS = ['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'LABEL', 'OPTION', 'VIDEO', 'AUDIO']
    var INTERACTIVE_ROLES = ['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio', 'switch', 'option', 'slider']

    function isDeadClick(element) {
        var current = element
        for (var depth = 0; depth < 5 && current && current !== document.body; depth++) {
            if (INTERACTIVE_TAGS.indexOf(current.tagName) !== -1) return false
            var role = current.getAttribute ? current.getAttribute('role') : null
            if (role && INTERACTIVE_ROLES.indexOf(role) !== -1) return false
            if (current.onclick || (current.getAttribute && current.getAttribute('onclick'))) return false
            if (current.tabIndex >= 0 && current.hasAttribute && current.hasAttribute('tabindex')) return false
            if (current.isContentEditable) return false
            try {
                if (window.getComputedStyle(current).cursor === 'pointer') return false
            } catch (e) {}
            current = current.parentElement
        }
        return true
    }

    var recentClicks = []

    function detectRageClick(pageX, pageY, now) {
        recentClicks.push({ time: now, x: pageX, y: pageY })
        recentClicks = recentClicks.filter(function (click) {
            return now - click.time < config.rage_click_window_ms
        })
        var clustered = recentClicks.filter(function (click) {
            return (
                Math.abs(click.x - pageX) < config.rage_click_radius_px && Math.abs(click.y - pageY) < config.rage_click_radius_px
            )
        })
        if (clustered.length >= config.rage_click_count) {
            recentClicks = []
            return true
        }
        return false
    }

    function onClick(event) {
        var target = event.target
        if (!target || target.nodeType !== 1) return
        var clientX = event.clientX || 0
        var clientY = event.clientY || 0
        var pageX = Math.round(event.pageX || clientX + (window.scrollX || 0))
        var pageY = Math.round(event.pageY || clientY + (window.scrollY || 0))
        var now = Date.now()
        var rage = detectRageClick(pageX, pageY, now)
        var dead = isDeadClick(target)
        var props = {
            $event_type: 'click',
            $el_tag_name: target.tagName.toLowerCase(),
            $el_selector: cssSelector(target),
            $el_text: elementText(target),
            $el_id: target.id || '',
            $el_class: typeof target.className === 'string' ? target.className : '',
            $el_href: target.href || (target.closest && target.closest('a') ? target.closest('a').href : ''),
            $is_dead_click: dead,
            $client_x: clientX,
            $client_y: clientY,
            $page_x: pageX,
            $page_y: pageY,
            $x_ratio: +(clientX / (window.innerWidth || 1)).toFixed(4),
            $y_ratio: +(clientY / (window.innerHeight || 1)).toFixed(4),
        }
        record('$autocapture', props)
        if (rage) record('$rageclick', props)
        hud.recordClick({ x: pageX, y: pageY, time: now, isRage: rage, isDead: dead })
    }

    function onSubmit(event) {
        var form = event.target
        if (!form || form.tagName !== 'FORM') return
        record('$form_submit', {
            $form_id: form.id || '',
            $form_name: form.getAttribute('name') || '',
            $form_action: form.getAttribute('action') || '',
            $form_method: (form.getAttribute('method') || 'GET').toUpperCase(),
        })
    }

    function setupAutocapture() {
        if (!config.autocapture) return
        document.addEventListener('click', onClick, true)
        document.addEventListener('submit', onSubmit, true)
    }

    // ----- Page views, scroll depth, time on page -----------------------------------------------

    var pageStartTime = Date.now()
    var currentPath = window.location.pathname
    var maxScrollDepth = 0
    var pageviewSent = false

    function updateScrollDepth() {
        var root = document.documentElement
        var scrollable = (root.scrollHeight || 0) - (root.clientHeight || 0)
        if (scrollable <= 0) {
            maxScrollDepth = Math.max(maxScrollDepth, 100)
            return
        }
        var position = window.scrollY || root.scrollTop || document.body.scrollTop || 0
        var percent = Math.round((position / scrollable) * 100)
        maxScrollDepth = Math.max(maxScrollDepth, Math.min(100, Math.max(0, percent)))
    }

    function capturePageview() {
        pageStartTime = Date.now()
        currentPath = window.location.pathname
        maxScrollDepth = 0
        pageviewSent = true
        record('$pageview', { $pathname: currentPath, $title: document.title || '', $href: window.location.href })
        setTimeout(updateScrollDepth, 0)
    }

    var leaveSent = false

    // Each $pageleave reports the time since the page was shown (or re-shown after the tab was
    // hidden), so a visitor who switches tabs twice is not counted twice.
    function capturePageleave(final) {
        if (!config.capture_pageleave || !pageviewSent || leaveSent) return
        var segmentSeconds = Math.round((Date.now() - pageStartTime) / 1000)
        if (!final && segmentSeconds < 1) return
        updateScrollDepth()
        leaveSent = true
        record('$pageleave', {
            $pathname: currentPath,
            $duration_seconds: segmentSeconds,
            $scroll_depth_percent: maxScrollDepth,
        })
        flush(true)
        pageStartTime = Date.now()
        if (!final) {
            leaveSent = false
        }
    }

    function routeChanged() {
        if (window.location.pathname === currentPath) return
        capturePageleave(false)
        capturePageview()
    }

    function setupHistoryListeners() {
        var pushState = window.history.pushState
        if (pushState) {
            window.history.pushState = function () {
                var result = pushState.apply(this, arguments)
                routeChanged()
                return result
            }
        }
        var replaceState = window.history.replaceState
        if (replaceState) {
            window.history.replaceState = function () {
                var result = replaceState.apply(this, arguments)
                routeChanged()
                return result
            }
        }
        window.addEventListener('popstate', routeChanged)
    }

    function setupErrorCapture() {
        if (!config.capture_errors) return
        window.addEventListener('error', function (event) {
            record('$exception', {
                $exception_message: event.message || 'Unknown error',
                $exception_filename: event.filename || '',
                $exception_lineno: event.lineno || 0,
                $exception_colno: event.colno || 0,
                $exception_type: event.error && event.error.name ? event.error.name : 'Error',
                $exception_stack: event.error && event.error.stack ? String(event.error.stack).substring(0, MAX_STACK_LENGTH) : '',
            })
        })
        window.addEventListener('unhandledrejection', function (event) {
            var reason = event.reason || {}
            record('$exception', {
                $exception_message: reason.message || String(reason),
                $exception_type: 'UnhandledPromiseRejection',
                $exception_stack: reason.stack ? String(reason.stack).substring(0, MAX_STACK_LENGTH) : '',
            })
        })
    }

    // ----- Debug HUD: an on-page overlay of this visitor's clicks (Alt+H) -----------------------

    var hud = (function () {
        var clicks = []
        var visible = false
        var filter = 'all'
        var canvas = null
        var context = null
        var root = null
        var frame = null

        try {
            var saved = sessionStorage.getItem(HUD_CLICKS_KEY)
            if (saved) clicks = JSON.parse(saved)
        } catch (e) {}

        function persist() {
            try {
                if (clicks.length > 250) clicks = clicks.slice(-250)
                sessionStorage.setItem(HUD_CLICKS_KEY, JSON.stringify(clicks))
            } catch (e) {}
        }

        function buttonStyle(active, color) {
            return (
                'flex:1;border:0;border-radius:6px;padding:6px 0;font:600 11px/1.2 inherit;cursor:pointer;' +
                (active ? 'background:' + color + ';color:#fff;' : 'background:none;color:#71717A;')
            )
        }

        function statBox(label, id, color) {
            return (
                '<div style="background:#18181B;border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:8px;text-align:center;">' +
                '<div style="font-size:10px;color:#71717A;text-transform:uppercase;">' +
                label +
                '</div><div id="' +
                id +
                '" style="font-size:16px;font-weight:700;color:' +
                color +
                ';">0</div></div>'
            )
        }

        function build() {
            if (root || !document.body) return
            canvas = document.createElement('canvas')
            canvas.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483640;display:none;'
            document.body.appendChild(canvas)
            context = canvas.getContext('2d')

            root = document.createElement('div')
            root.id = '__hotspot_hud'
            root.style.cssText =
                'position:fixed;bottom:20px;right:20px;z-index:2147483645;width:min(300px,calc(100vw - 40px));background:#111113;color:#FAFAFA;' +
                'border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:14px;box-shadow:0 20px 50px rgba(0,0,0,.6);' +
                'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:12px;'
            root.innerHTML =
                '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">' +
                '<strong style="font-size:13px;">Hotspot debug</strong>' +
                '<button data-hud="close" style="background:none;border:0;color:#71717A;cursor:pointer;font-size:16px;">&times;</button></div>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px;">' +
                statBox('Clicks', '__hotspot_stat_total', '#2563EB') +
                statBox('Rage', '__hotspot_stat_rage', '#EF4444') +
                statBox('Dead', '__hotspot_stat_dead', '#F59E0B') +
                '</div>' +
                '<div style="display:flex;gap:4px;margin-bottom:12px;background:#18181B;padding:3px;border-radius:8px;">' +
                '<button data-hud="filter" data-filter="all" style="' + buttonStyle(true, '#2563EB') + '">All</button>' +
                '<button data-hud="filter" data-filter="rage" style="' + buttonStyle(false, '#EF4444') + '">Rage</button>' +
                '<button data-hud="filter" data-filter="dead" style="' + buttonStyle(false, '#F59E0B') + '">Dead</button></div>' +
                '<div style="display:flex;gap:8px;">' +
                '<button data-hud="clear" style="flex:1;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);color:#FAFAFA;border-radius:8px;padding:7px 0;font:600 11px inherit;cursor:pointer;">Clear</button>' +
                '<a href="' + config.api_host + '/dashboard/" target="_blank" rel="noopener" style="flex:1;display:flex;align-items:center;justify-content:center;text-decoration:none;background:rgba(37,99,235,.15);border:1px solid rgba(37,99,235,.4);color:#60A5FA;border-radius:8px;padding:7px 0;font-weight:600;">Open dashboard</a></div>' +
                '<p style="margin:10px 0 0;color:#71717A;font-size:11px;">Only you can see this overlay. Press Alt+H to hide it.</p>'
            document.body.appendChild(root)

            root.addEventListener('click', function (event) {
                var button = event.target.closest ? event.target.closest('[data-hud]') : null
                if (!button) return
                event.stopPropagation()
                var action = button.getAttribute('data-hud')
                if (action === 'close') toggle(false)
                if (action === 'clear') clear()
                if (action === 'filter') {
                    filter = button.getAttribute('data-filter') || 'all'
                    var buttons = root.querySelectorAll('[data-hud="filter"]')
                    for (var i = 0; i < buttons.length; i++) {
                        var mode = buttons[i].getAttribute('data-filter')
                        var color = mode === 'rage' ? '#EF4444' : mode === 'dead' ? '#F59E0B' : '#2563EB'
                        buttons[i].style.cssText = buttonStyle(mode === filter, color)
                    }
                    render()
                }
            })
            window.addEventListener('resize', render)
            window.addEventListener('scroll', render, { passive: true })
        }

        function updateStats() {
            if (!root) return
            var rage = 0
            var dead = 0
            for (var i = 0; i < clicks.length; i++) {
                if (clicks[i].isRage) rage++
                if (clicks[i].isDead) dead++
            }
            document.getElementById('__hotspot_stat_total').textContent = String(clicks.length)
            document.getElementById('__hotspot_stat_rage').textContent = String(rage)
            document.getElementById('__hotspot_stat_dead').textContent = String(dead)
        }

        function glow(x, y, radius, color) {
            var gradient = context.createRadialGradient(x, y, 0, x, y, radius)
            gradient.addColorStop(0, 'rgba(' + color + ',.85)')
            gradient.addColorStop(0.4, 'rgba(' + color + ',.35)')
            gradient.addColorStop(1, 'rgba(' + color + ',0)')
            context.fillStyle = gradient
            context.beginPath()
            context.arc(x, y, radius, 0, Math.PI * 2)
            context.fill()
        }

        function render() {
            if (!visible || !canvas || !context) return
            if (frame) cancelAnimationFrame(frame)
            frame = requestAnimationFrame(function () {
                var ratio = window.devicePixelRatio || 1
                var width = window.innerWidth
                var height = window.innerHeight
                canvas.width = width * ratio
                canvas.height = height * ratio
                context.setTransform(ratio, 0, 0, ratio, 0, 0)
                context.clearRect(0, 0, width, height)
                var scrollX = window.scrollX || 0
                var scrollY = window.scrollY || 0
                for (var i = 0; i < clicks.length; i++) {
                    var click = clicks[i]
                    if (filter === 'rage' && !click.isRage) continue
                    if (filter === 'dead' && !click.isDead) continue
                    var x = click.x - scrollX
                    var y = click.y - scrollY
                    if (x < -60 || x > width + 60 || y < -60 || y > height + 60) continue
                    if (click.isRage) glow(x, y, 45, '239,68,68')
                    else if (click.isDead) glow(x, y, 35, '245,158,11')
                    else glow(x, y, 30, '37,99,235')
                }
            })
        }

        function toggle(force) {
            visible = typeof force === 'boolean' ? force : !visible
            build()
            if (canvas) canvas.style.display = visible ? 'block' : 'none'
            if (root) root.style.display = visible ? 'block' : 'none'
            updateStats()
            render()
        }

        function clear() {
            clicks = []
            persist()
            updateStats()
            render()
        }

        return {
            init: function () {
                if (!config.debug_hud) return
                window.addEventListener('keydown', function (event) {
                    if (event.altKey && (event.key === 'h' || event.key === 'H' || event.keyCode === 72)) toggle()
                })
                var start = function () {
                    toggle(true)
                }
                if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start)
                else start()
            },
            recordClick: function (click) {
                if (!config.debug_hud) return
                clicks.push(click)
                persist()
                updateStats()
                render()
            },
            toggle: toggle,
            clear: clear,
            getClicks: function () {
                return clicks.slice()
            },
        }
    })()

    // ----- Public API ---------------------------------------------------------------------------

    var hotspot = {
        version: VERSION,
        init: function (apiKey, options) {
            if (apiKey) config.api_key = apiKey
            if (options && typeof options === 'object') {
                for (var name in options) {
                    if (Object.prototype.hasOwnProperty.call(options, name)) config[name] = options[name]
                }
                if (options.api_host) config.api_host = String(options.api_host).replace(/\/+$/, '')
            }
            if (config.capture_pageview && !pageviewSent) capturePageview()
        },
        capture: function (eventName, properties) {
            if (typeof eventName === 'string' && eventName) record(eventName, properties || {})
        },
        identify: function (distinctId, properties) {
            if (!distinctId) return
            var previous = getDistinctId()
            storage.set(DISTINCT_ID_KEY, String(distinctId))
            record('$identify', { $anon_distinct_id: previous, $identified_id: String(distinctId), $set: properties || {} })
        },
        setPersonProperties: function (properties) {
            record('$set', { $set: properties || {} })
        },
        register: function (properties) {
            if (properties && typeof properties === 'object') setSuperProperties(properties)
        },
        reset: function () {
            storage.remove(DISTINCT_ID_KEY)
            storage.remove(SESSION_ID_KEY)
            storage.remove(SESSION_TIMESTAMP_KEY)
            storage.remove(SUPER_PROPS_KEY)
        },
        optOut: function () {
            storage.set(OPT_OUT_KEY, 'true')
            queue = []
        },
        optIn: function () {
            storage.remove(OPT_OUT_KEY)
        },
        hasOptedOut: function () {
            return storage.get(OPT_OUT_KEY) === 'true'
        },
        getDistinctId: getDistinctId,
        getSessionId: getSessionId,
        on: function (eventName, callback) {
            if (typeof callback !== 'function') return
            if (!listeners[eventName]) listeners[eventName] = []
            listeners[eventName].push(callback)
        },
        flush: function () {
            flush(false)
        },
        debug: {
            show: function () {
                config.debug_hud = true
                hud.toggle(true)
            },
            hide: function () {
                hud.toggle(false)
            },
            clear: hud.clear,
            getClicks: hud.getClicks,
        },
    }

    window.hotspot = hotspot

    setupAutocapture()
    setupHistoryListeners()
    setupErrorCapture()
    window.addEventListener('scroll', updateScrollDepth, { passive: true })
    window.addEventListener('pagehide', function () {
        capturePageleave(true)
    })
    window.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            capturePageleave(false)
        } else {
            pageStartTime = Date.now()
        }
    })
    hud.init()

    if (config.api_key && config.capture_pageview) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', capturePageview)
        else capturePageview()
    }
})(window, document)
