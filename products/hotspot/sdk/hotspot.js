/**
 * Hotspot web script
 *
 * One script tag records page views, clicks (with rage click and dead click detection), form
 * submits, scroll depth, time on page, web vitals, JavaScript errors and single-page-app route
 * changes, and sends them in batches to your own Hotspot server. It also records the page itself
 * (the DOM, inputs, scrolling, mouse movement, console output and network requests) so the
 * dashboard can replay a session exactly as the visitor saw it.
 *
 *   <script src="https://your-hotspot-host/hotspot.js" data-api-key="YOUR_API_KEY" defer></script>
 *
 * Optional attributes: data-api-host (defaults to the host the script was loaded from),
 * data-autocapture="false", data-capture-errors="false", data-capture-pageview="false",
 * data-replay="false" (no DOM recording), data-mask-inputs="all" (mask every input value),
 * data-mask-text="true" (mask every text node), data-capture-console="false",
 * data-capture-network="false", data-capture-web-vitals="false", data-debug="true" (console
 * logging), data-debug-hud="true" (on-page click overlay, Alt+H).
 *
 * Markup: data-hotspot-mask on an element masks its text and input values, data-hotspot-block
 * replaces it with an empty box of the same size, data-hotspot-ignore skips its input events.
 * Password fields and payment fields (autocomplete="cc-*") are always masked.
 *
 * The visitor id and session id live in localStorage (cookie fallback). No third-party cookies.
 */
;(function (window, document) {
    'use strict'

    if (!window || !document || window.__hotspot_loaded) {
        return
    }

    window.__hotspot_loaded = true

    var VERSION = '3.1.0'
    var STORAGE_PREFIX = '__hotspot_'
    var DISTINCT_ID_KEY = STORAGE_PREFIX + 'distinct_id'
    var SESSION_ID_KEY = STORAGE_PREFIX + 'session_id'
    var SESSION_TIMESTAMP_KEY = STORAGE_PREFIX + 'session_ts'
    var DEVICE_ID_KEY = STORAGE_PREFIX + 'device_id'
    var OPT_OUT_KEY = STORAGE_PREFIX + 'opt_out'
    var SUPER_PROPS_KEY = STORAGE_PREFIX + 'super_props'
    var IDENTITY_TRAITS_KEY = STORAGE_PREFIX + 'identity_traits'
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
        session_replay: true,
        mask_all_inputs: false,
        mask_all_text: false,
        capture_console: true,
        capture_network: true,
        capture_web_vitals: true,
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
        if (isFalse(attribute('data-replay'))) config.session_replay = false
        if (attribute('data-mask-inputs') === 'all') config.mask_all_inputs = true
        if (attribute('data-mask-text') === 'true') config.mask_all_text = true
        if (isFalse(attribute('data-capture-console'))) config.capture_console = false
        if (isFalse(attribute('data-capture-network'))) config.capture_network = false
        if (isFalse(attribute('data-capture-web-vitals'))) config.capture_web_vitals = false
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

        // Payment redirect continuity: check for hs_ref in URL params (e.g. return from Stripe/Paddle)
        if (window.location && window.location.search) {
            try {
                var refMatch = window.location.search.match(/[?&]hs_ref=([a-zA-Z0-9_-]{8,64})/)
                if (refMatch && refMatch[1]) {
                    sessionId = refMatch[1]
                    storage.set(SESSION_ID_KEY, sessionId)
                }
            } catch (e) {}
        }

        if (!sessionId || now - lastSeen > config.session_timeout_ms) {
            sessionId = uuidv4()
            storage.set(SESSION_ID_KEY, sessionId)
        }
        storage.set(SESSION_TIMESTAMP_KEY, String(now))
        return sessionId
    }

    function getStoredTraits() {
        try {
            var raw = storage.get(IDENTITY_TRAITS_KEY)
            return raw ? JSON.parse(raw) : {}
        } catch (e) {
            return {}
        }
    }

    function setStoredTraits(traits) {
        if (!traits || typeof traits !== 'object') return
        var current = getStoredTraits()
        for (var name in traits) {
            if (Object.prototype.hasOwnProperty.call(traits, name)) current[name] = traits[name]
        }
        try {
            storage.set(IDENTITY_TRAITS_KEY, JSON.stringify(current))
        } catch (e) {}
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
        vitals.report(currentPath)
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
        vitals.reset()
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
            var type = event.error && event.error.name ? event.error.name : 'Error'
            var message = event.message || 'Unknown error'
            record('$exception', {
                $exception_message: message,
                $exception_filename: event.filename || '',
                $exception_lineno: event.lineno || 0,
                $exception_colno: event.colno || 0,
                $exception_type: type,
                $exception_stack: event.error && event.error.stack ? String(event.error.stack).substring(0, MAX_STACK_LENGTH) : '',
            })
            replay.addCustom('console', { level: 'error', message: 'Uncaught ' + type + ': ' + message, t: Date.now() })
        })
        window.addEventListener('unhandledrejection', function (event) {
            var reason = event.reason || {}
            var message = reason.message || String(reason)
            record('$exception', {
                $exception_message: message,
                $exception_type: 'UnhandledPromiseRejection',
                $exception_stack: reason.stack ? String(reason.stack).substring(0, MAX_STACK_LENGTH) : '',
            })
            replay.addCustom('console', { level: 'error', message: 'Unhandled promise rejection: ' + message, t: Date.now() })
        })
    }

    // ----- Console and network capture: shown next to the replay ---------------------------------

    var MAX_CONSOLE_MESSAGE_LENGTH = 2000
    var MAX_CONSOLE_ENTRIES = 500
    var MAX_NETWORK_ENTRIES = 500
    var MAX_URL_LENGTH = 500

    function describeValue(value) {
        if (value === null || value === undefined) return String(value)
        if (typeof value === 'string') return value
        if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
        if (typeof value === 'function') return '[function ' + (value.name || '') + ']'
        if (value instanceof Error) {
            var stack = value.stack ? '\n' + String(value.stack).split('\n').slice(1, 6).join('\n') : ''
            return (value.name || 'Error') + ': ' + value.message + stack
        }
        if (value.nodeType === 1 && value.tagName) return '<' + value.tagName.toLowerCase() + (value.id ? '#' + value.id : '') + '>'
        try {
            return JSON.stringify(value, function (key, inner) {
                return typeof inner === 'bigint' ? String(inner) : inner
            })
        } catch (e) {
            return Object.prototype.toString.call(value)
        }
    }

    function formatConsoleArgs(args) {
        var parts = []
        for (var i = 0; i < args.length; i++) parts.push(describeValue(args[i]))
        return parts.join(' ').substring(0, MAX_CONSOLE_MESSAGE_LENGTH)
    }

    function setupConsoleCapture() {
        if (!config.capture_console || !window.console) return
        var levels = ['log', 'info', 'warn', 'error', 'debug']
        var count = 0
        levels.forEach(function (level) {
            var original = window.console[level]
            if (typeof original !== 'function') return
            window.console[level] = function () {
                var args = Array.prototype.slice.call(arguments)
                try {
                    original.apply(window.console, args)
                } catch (e) {}
                if (typeof args[0] === 'string' && args[0].indexOf('[Hotspot]') === 0) return
                if (count >= MAX_CONSOLE_ENTRIES) return
                count++
                var message = formatConsoleArgs(args)
                if (count === MAX_CONSOLE_ENTRIES) {
                    message = 'Hotspot stopped capturing console output on this page after ' + MAX_CONSOLE_ENTRIES + ' entries'
                    level = 'warn'
                }
                replay.addCustom('console', { level: level, message: message, t: Date.now() })
            }
        })
    }

    function trimUrl(url) {
        try {
            var parsed = new URL(String(url), window.location.href)
            parsed.username = ''
            parsed.password = ''
            return parsed.href.substring(0, MAX_URL_LENGTH)
        } catch (e) {
            return String(url).substring(0, MAX_URL_LENGTH)
        }
    }

    function isHotspotRequest(url) {
        return typeof url === 'string' && url.indexOf(config.api_host) === 0
    }

    // Only the method, URL, status and timing of each request are kept; bodies and headers never are.
    function setupNetworkCapture() {
        if (!config.capture_network) return
        var count = 0
        function note(entry) {
            if (count >= MAX_NETWORK_ENTRIES) return
            count++
            replay.addCustom('network', entry)
        }
        if (window.fetch) {
            var originalFetch = window.fetch
            window.fetch = function (input, init) {
                var url = typeof input === 'string' ? input : input && input.url ? input.url : String(input)
                var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase()
                var started = Date.now()
                var result = originalFetch.apply(this, arguments)
                if (!isHotspotRequest(url) && result && typeof result.then === 'function') {
                    result.then(
                        function (response) {
                            note({ kind: 'fetch', method: method, url: trimUrl(url), status: response.status, ok: response.ok, duration_ms: Date.now() - started, t: started })
                        },
                        function () {
                            note({ kind: 'fetch', method: method, url: trimUrl(url), status: 0, ok: false, duration_ms: Date.now() - started, t: started })
                        }
                    )
                }
                return result
            }
        }
        if (window.XMLHttpRequest && XMLHttpRequest.prototype) {
            var open = XMLHttpRequest.prototype.open
            var send = XMLHttpRequest.prototype.send
            XMLHttpRequest.prototype.open = function (method, url) {
                this.__hotspot = { method: String(method).toUpperCase(), url: String(url) }
                return open.apply(this, arguments)
            }
            XMLHttpRequest.prototype.send = function () {
                var request = this
                var meta = request.__hotspot
                if (meta && !isHotspotRequest(meta.url)) {
                    var started = Date.now()
                    request.addEventListener('loadend', function () {
                        note({ kind: 'xhr', method: meta.method, url: trimUrl(meta.url), status: request.status, ok: request.status >= 200 && request.status < 400, duration_ms: Date.now() - started, t: started })
                    })
                }
                return send.apply(this, arguments)
            }
        }
    }

    // ----- Web vitals ---------------------------------------------------------------------------
    //
    // LCP, FCP and TTFB describe the first load of the document, so they are reported once. CLS and
    // INP accumulate while a path is shown and start again on every route change.

    var vitals = (function () {
        var supported = typeof window.PerformanceObserver === 'function'
        var page = null
        var load = { lcp: null, fcp: null, ttfb: null, reported: false }
        var hiddenSpans = []

        function now() {
            return window.performance && performance.now ? performance.now() : Date.now()
        }

        // An interaction in a hidden tab is not painted until the tab is shown again, so its duration
        // measures the time away rather than the page's responsiveness.
        function overlapsHiddenTab(start, end) {
            for (var i = 0; i < hiddenSpans.length; i++) {
                if (start < hiddenSpans[i][1] && end > hiddenSpans[i][0]) return true
            }
            return false
        }

        function observe(type, options, callback) {
            try {
                var observer = new PerformanceObserver(function (list) {
                    callback(list.getEntries())
                })
                observer.observe(merge({ type: type, buffered: true }, options))
            } catch (e) {}
        }

        function newPage() {
            return { cls: 0, clsWindow: 0, clsWindowStart: 0, clsWindowLast: 0, interactions: {}, inp: null, sent: false }
        }

        // CLS groups shifts into windows of at most 5 s with gaps under 1 s and keeps the worst window.
        function noteShift(entry) {
            if (!page || entry.hadRecentInput) return
            var start = entry.startTime
            if (page.clsWindow && start - page.clsWindowLast < 1000 && start - page.clsWindowStart < 5000) {
                page.clsWindow += entry.value
            } else {
                page.clsWindow = entry.value
                page.clsWindowStart = start
            }
            page.clsWindowLast = start
            page.cls = Math.max(page.cls, page.clsWindow)
        }

        // INP is the slowest interaction until there are 50, then a high percentile instead of the worst.
        function noteInteraction(entry) {
            if (!page || !entry.interactionId) return
            if (overlapsHiddenTab(entry.startTime, entry.startTime + entry.duration)) return
            page.interactions[entry.interactionId] = Math.max(page.interactions[entry.interactionId] || 0, entry.duration)
            var durations = []
            for (var key in page.interactions) {
                if (Object.prototype.hasOwnProperty.call(page.interactions, key)) durations.push(page.interactions[key])
            }
            durations.sort(function (a, b) {
                return b - a
            })
            page.inp = durations[Math.min(durations.length - 1, Math.floor(durations.length / 50))]
        }

        function round(value, digits) {
            if (typeof value !== 'number' || !isFinite(value)) return null
            var factor = Math.pow(10, digits)
            return Math.round(value * factor) / factor
        }

        return {
            init: function () {
                if (!config.capture_web_vitals || !supported) return
                page = newPage()
                if (document.visibilityState === 'hidden') hiddenSpans.push([0, Infinity])
                document.addEventListener('visibilitychange', function () {
                    var moment = now()
                    if (document.visibilityState === 'hidden') hiddenSpans.push([moment, Infinity])
                    else if (hiddenSpans.length) hiddenSpans[hiddenSpans.length - 1][1] = moment
                })
                try {
                    var navigation = performance.getEntriesByType('navigation')[0]
                    if (navigation && navigation.responseStart > 0) load.ttfb = navigation.responseStart
                } catch (e) {}
                observe('paint', {}, function (entries) {
                    for (var i = 0; i < entries.length; i++) {
                        if (entries[i].name === 'first-contentful-paint') load.fcp = entries[i].startTime
                    }
                })
                observe('largest-contentful-paint', {}, function (entries) {
                    if (entries.length) load.lcp = entries[entries.length - 1].startTime
                })
                observe('layout-shift', {}, function (entries) {
                    for (var i = 0; i < entries.length; i++) noteShift(entries[i])
                })
                observe('event', { durationThreshold: 40 }, function (entries) {
                    for (var i = 0; i < entries.length; i++) noteInteraction(entries[i])
                })
                observe('first-input', {}, function (entries) {
                    for (var i = 0; i < entries.length; i++) noteInteraction(entries[i])
                })
            },
            // One $web_vitals event per page, sent the first time the page is left or hidden.
            report: function (pathname) {
                if (!page || page.sent) return
                page.sent = true
                var props = {
                    $pathname: pathname,
                    $cls: round(page.cls, 4),
                    $inp_ms: round(page.inp, 0),
                    $lcp_ms: null,
                    $fcp_ms: null,
                    $ttfb_ms: null,
                }
                if (!load.reported) {
                    load.reported = true
                    props.$lcp_ms = round(load.lcp, 0)
                    props.$fcp_ms = round(load.fcp, 0)
                    props.$ttfb_ms = round(load.ttfb, 0)
                }
                record('$web_vitals', props)
            },
            reset: function () {
                if (page) page = newPage()
            },
        }
    })()

    // ----- Session replay: DOM recording --------------------------------------------------------
    //
    // rrweb takes one snapshot of the DOM and then records every mutation, input, scroll and mouse
    // move, so the dashboard can rebuild the page as the visitor saw it. The recorder is a separate
    // file loaded from the Hotspot host on demand, so pages with replay turned off never download
    // it. Masking happens in the browser: masked values never leave the page.

    var REPLAY_FLUSH_MS = 5000
    var REPLAY_FLUSH_BYTES = 512 * 1024
    var REPLAY_BEACON_LIMIT = 60000
    var REPLAY_MAX_PENDING = 200
    var REPLAY_CHECKOUT_MS = 10 * 60 * 1000
    var MASK_SELECTOR = '[data-hotspot-mask], .hs-mask'
    var BLOCK_SELECTOR = '[data-hotspot-block], .hs-block'
    var SENSITIVE_AUTOCOMPLETE = /^(cc-|one-time-code|current-password|new-password)/

    function shouldMaskInput(element) {
        if (config.mask_all_inputs) return true
        if (!element || typeof element.getAttribute !== 'function') return false
        if ((element.getAttribute('type') || '').toLowerCase() === 'password') return true
        if (SENSITIVE_AUTOCOMPLETE.test((element.getAttribute('autocomplete') || '').toLowerCase())) return true
        return !!(element.closest && element.closest(MASK_SELECTOR))
    }

    function maskInputValue(text, element) {
        if (!shouldMaskInput(element)) return text
        return new Array(String(text).length + 1).join('*')
    }

    var replay = (function () {
        var buffer = []
        var bufferBytes = 0
        var bufferSession = null
        var currentSession = null
        var lastSessionCheck = 0
        var timer = null
        var stopRecording = null
        var starting = false
        var stopped = false
        var awaitingSnapshot = false
        var pending = []
        var uploads = Promise.resolve()
        var canGzip =
            typeof window.CompressionStream === 'function' &&
            typeof window.Response === 'function' &&
            typeof Blob.prototype.stream === 'function'

        function endpointFor(compressed) {
            return config.api_host + '/hotspot/replay/' + (compressed ? '?compression=gzip' : '')
        }

        function loadRecorder(callback) {
            if (window.rrwebRecord && window.rrwebRecord.record) {
                callback()
                return
            }
            var script = document.createElement('script')
            script.src = config.api_host + '/hotspot/assets/recorder.js'
            script.async = true
            script.onload = function () {
                if (window.rrwebRecord && window.rrwebRecord.record) callback()
                else log('The recorder loaded but did not register')
            }
            script.onerror = function () {
                log('Could not load the recorder from ' + script.src)
            }
            ;(document.head || document.documentElement).appendChild(script)
        }

        function recorderOptions() {
            return {
                emit: onEvent,
                maskAllInputs: true,
                maskInputFn: maskInputValue,
                maskTextSelector: config.mask_all_text ? '*' : MASK_SELECTOR,
                blockSelector: BLOCK_SELECTOR,
                blockClass: 'hs-block',
                ignoreSelector: '[data-hotspot-ignore]',
                inlineStylesheet: true,
                collectFonts: false,
                recordCanvas: false,
                slimDOMOptions: 'all',
                checkoutEveryNms: REPLAY_CHECKOUT_MS,
                sampling: { mousemove: 50, mouseInteraction: true, scroll: 150, media: 800, input: 'last' },
                errorHandler: function (error) {
                    log('Recorder error', error)
                    return true
                },
            }
        }

        // A session that expired while the tab stayed open gets a recording of its own, starting
        // with a fresh snapshot, so the dashboard can replay it without the previous session.
        function trackSession() {
            var now = Date.now()
            if (now - lastSessionCheck < 1000) return
            lastSessionCheck = now
            var sessionId = getSessionId()
            if (currentSession === null || sessionId === currentSession) {
                currentSession = sessionId
                return
            }
            flush('timer')
            currentSession = sessionId
            // Mutations without a snapshot cannot be replayed, so they are dropped until the snapshot
            // arrives. The snapshot is taken outside rrweb's own emit call so it cannot re-enter it.
            awaitingSnapshot = true
            setTimeout(function () {
                try {
                    window.rrwebRecord.record.takeFullSnapshot(true)
                } catch (e) {}
            }, 0)
        }

        function onEvent(event) {
            if (stopped) return
            trackSession()
            if (awaitingSnapshot) {
                if (event.type !== 4 && event.type !== 2) return
                if (event.type === 2) awaitingSnapshot = false
            }
            var text
            try {
                text = JSON.stringify(event)
            } catch (e) {
                return
            }
            if (!buffer.length) bufferSession = currentSession
            buffer.push(text)
            bufferBytes += text.length
            // A full snapshot is most of a recording; sending it at once lets the dashboard show the
            // page while the visitor is still on it.
            if (event.type === 2 || bufferBytes >= REPLAY_FLUSH_BYTES) {
                flush('timer')
            } else if (!timer) {
                timer = setTimeout(function () {
                    flush('timer')
                }, REPLAY_FLUSH_MS)
            }
        }

        // reason is 'timer' (the page is alive), 'hidden' (the tab went to the background) or
        // 'unload' (the page is going away and has one chance to send).
        function flush(reason) {
            if (timer) {
                clearTimeout(timer)
                timer = null
            }
            if (!buffer.length) return
            var events = buffer.join(',')
            var sessionId = bufferSession || getSessionId()
            buffer = []
            bufferBytes = 0
            bufferSession = null
            if (!config.api_key) return
            var body =
                '{"api_key":' + JSON.stringify(config.api_key) +
                ',"session_id":' + JSON.stringify(sessionId) +
                ',"distinct_id":' + JSON.stringify(getDistinctId()) +
                ',"events":[' + events + ']}'
            var small = body.length < REPLAY_BEACON_LIMIT
            if ((reason === 'unload' || reason === 'hidden') && small && beacon(body)) return
            if (reason === 'unload') {
                // Browsers cap keepalive requests at about 64 KB, so a larger tail is lost.
                post(body, endpointFor(false), true)
                return
            }
            // Uploads go out one after another so chunks reach the server in order even when a large
            // snapshot takes longer to compress than the small chunk after it.
            uploads = uploads
                .then(function () {
                    return canGzip ? compress(body).then(null, function () { return body }) : body
                })
                .then(function (payload) {
                    return post(payload, endpointFor(payload !== body), false)
                })
                .then(null, function () {})
        }

        function beacon(body) {
            if (!navigator.sendBeacon) return false
            try {
                return navigator.sendBeacon(endpointFor(false), new Blob([body], { type: 'text/plain;charset=UTF-8' }))
            } catch (e) {
                return false
            }
        }

        function compress(text) {
            var stream = new Blob([text]).stream().pipeThrough(new window.CompressionStream('gzip'))
            return new window.Response(stream).blob()
        }

        function post(payload, target, keepalive) {
            if (!window.fetch) return Promise.resolve()
            var request
            try {
                // text/plain keeps this a simple request, so the browser sends it without a preflight.
                request = window.fetch(target, {
                    method: 'POST',
                    body: payload,
                    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
                    mode: 'cors',
                    credentials: 'omit',
                    keepalive: !!keepalive,
                })
            } catch (e) {
                log('Recording upload failed', e)
                return Promise.resolve()
            }
            return request
                .then(function (response) {
                    if (response.status === 401 || response.status === 403) {
                        log('The server refused the recording (' + response.status + '); recording stopped')
                        stop()
                    } else if (!response.ok) {
                        log('Recording upload answered ' + response.status)
                    }
                })
                .catch(function (error) {
                    log('Recording upload failed', error)
                })
        }

        function start() {
            if (starting || stopped || stopRecording || !config.session_replay || !config.api_key) return
            if (storage.get(OPT_OUT_KEY) === 'true') return
            starting = true
            loadRecorder(function () {
                starting = false
                if (stopped || stopRecording) return
                try {
                    stopRecording = window.rrwebRecord.record(recorderOptions()) || function () {}
                } catch (error) {
                    log('The recorder failed to start', error)
                    return
                }
                log('Recording the session')
                var queued = pending
                pending = []
                for (var i = 0; i < queued.length; i++) addCustom(queued[i][0], queued[i][1])
            })
        }

        function stop() {
            stopped = true
            if (stopRecording) {
                try {
                    stopRecording()
                } catch (e) {}
                stopRecording = null
            }
            if (timer) {
                clearTimeout(timer)
                timer = null
            }
            buffer = []
            bufferBytes = 0
            pending = []
        }

        // Also the way a page with data-replay="false" starts recording after consent.
        function resume() {
            stopped = false
            config.session_replay = true
            start()
        }

        // Console output and network requests ride along in the recording as rrweb custom events.
        function addCustom(tag, payload) {
            if (stopped || !config.session_replay) return
            if (!stopRecording) {
                if (pending.length < REPLAY_MAX_PENDING) pending.push([tag, payload])
                return
            }
            try {
                window.rrwebRecord.record.addCustomEvent(tag, payload)
            } catch (e) {}
        }

        return {
            start: start,
            stop: stop,
            resume: resume,
            flush: flush,
            addCustom: addCustom,
            isRecording: function () {
                return !!stopRecording
            },
        }
    })()

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
            canvas.setAttribute('data-hotspot-block', '')
            document.body.appendChild(canvas)
            context = canvas.getContext('2d')

            root = document.createElement('div')
            root.id = '__hotspot_hud'
            root.setAttribute('data-hotspot-block', '')
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
            replay.start()
        },
        capture: function (eventName, properties) {
            if (typeof eventName === 'string' && eventName) record(eventName, properties || {})
        },
        identify: function (distinctId, properties, options) {
            if (!distinctId) return
            var previous = getDistinctId()
            var currentId = String(distinctId)
            var props = properties && typeof properties === 'object' ? properties : {}

            // Account switching defense: if user identity changed in same browser, rotate session immediately
            var storedId = storage.get(DISTINCT_ID_KEY)
            if (storedId && storedId !== currentId && storedId !== previous) {
                storage.set(SESSION_ID_KEY, uuidv4())
            }

            storage.set(DISTINCT_ID_KEY, currentId)
            setStoredTraits(props)

            var payload = {
                $anon_distinct_id: previous !== currentId ? previous : null,
                $identified_id: currentId,
                $set: props,
            }
            if (options && options.hashIdentifier) {
                payload.$hash_identifier = true
            }
            record('$identify', payload)
        },
        setPersonProperties: function (properties) {
            if (properties && typeof properties === 'object') {
                setStoredTraits(properties)
                record('$set', { $set: properties })
            }
        },
        register: function (properties) {
            if (properties && typeof properties === 'object') setSuperProperties(properties)
        },
        reset: function () {
            storage.remove(DISTINCT_ID_KEY)
            storage.remove(SESSION_ID_KEY)
            storage.remove(SESSION_TIMESTAMP_KEY)
            storage.remove(SUPER_PROPS_KEY)
            storage.remove(IDENTITY_TRAITS_KEY)
            // Immediately start a clean anonymous session to prevent cross-account contamination
            var newAnonId = uuidv4()
            var newSessionId = uuidv4()
            storage.set(DISTINCT_ID_KEY, newAnonId)
            storage.set(SESSION_ID_KEY, newSessionId)
            storage.set(SESSION_TIMESTAMP_KEY, String(Date.now()))
        },
        trackPurchase: function (details) {
            if (!details || typeof details !== 'object') return
            // Zero-PII sanitization: ensure no raw card numbers, cvv, secrets
            var safe = {}
            for (var key in details) {
                if (Object.prototype.hasOwnProperty.call(details, key)) {
                    if (/card|num|cvv|expir|passw|token|secret|pin|ssn|auth/i.test(key)) continue
                    safe[key] = details[key]
                }
            }
            if (!safe.plan && safe.tier) safe.plan = safe.tier
            record('$purchase', safe)
        },
        trackPaymentFailure: function (details) {
            if (!details || typeof details !== 'object') return
            var safe = {}
            for (var key in details) {
                if (Object.prototype.hasOwnProperty.call(details, key)) {
                    if (/card|num|cvv|expir|passw|token|secret|pin|ssn|auth/i.test(key)) continue
                    safe[key] = details[key]
                }
            }
            record('$payment_failed', safe)
        },
        trackHesitation: function (details) {
            record('$price_hesitation', details || {})
        },
        optOut: function () {
            storage.set(OPT_OUT_KEY, 'true')
            queue = []
            replay.stop()
        },
        optIn: function () {
            storage.remove(OPT_OUT_KEY)
            replay.resume()
        },
        hasOptedOut: function () {
            return storage.get(OPT_OUT_KEY) === 'true'
        },
        startSessionRecording: replay.resume,
        stopSessionRecording: replay.stop,
        isSessionRecording: replay.isRecording,
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
    setupConsoleCapture()
    setupNetworkCapture()
    vitals.init()
    window.addEventListener('scroll', updateScrollDepth, { passive: true })
    window.addEventListener('pagehide', function () {
        capturePageleave(true)
        replay.flush('unload')
    })
    window.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            capturePageleave(false)
            replay.flush('hidden')
        } else {
            pageStartTime = Date.now()
        }
    })
    hud.init()

    if (config.api_key && config.capture_pageview) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', capturePageview)
        else capturePageview()
    }
    replay.start()
})(window, document)
