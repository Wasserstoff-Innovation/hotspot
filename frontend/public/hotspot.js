/**
 * Hotspot - Modern Web Analytics & User Journey Tracking SDK
 * https://github.com/posthog/posthog (Hotspot edition)
 *
 * Lightweight, zero-dependency, single-script tracking for:
 * - Autocapture (clicks, forms, navigation)
 * - Pageviews & Active Duration
 * - Heatmaps & Click Coordinate Mapping
 * - Custom Events & User Identity
 * - Exceptions & Performance
 *
 * Usage:
 * <script src="/hotspot.js" data-api-key="YOUR_API_KEY" data-api-host="http://localhost:8000"></script>
 */
;(function (window, document) {
    'use strict'

    if (window.__hotspot_loaded) {
        return
    }
    window.__hotspot_loaded = true

    // Storage keys
    var STORAGE_PREFIX = '__hotspot_'
    var DISTINCT_ID_KEY = STORAGE_PREFIX + 'distinct_id'
    var SESSION_ID_KEY = STORAGE_PREFIX + 'session_id'
    var SESSION_TIMESTAMP_KEY = STORAGE_PREFIX + 'session_ts'
    var DEVICE_ID_KEY = STORAGE_PREFIX + 'device_id'
    var OPT_OUT_KEY = STORAGE_PREFIX + 'opt_out'
    var SUPER_PROPS_KEY = STORAGE_PREFIX + 'super_props'
    var QUEUE_KEY = STORAGE_PREFIX + 'event_queue'

    // Configuration defaults
    var config = {
        api_key: '',
        api_host: '',
        autocapture: true,
        capture_pageview: true,
        capture_pageleave: true,
        capture_heatmaps: true,
        capture_errors: true,
        capture_performance: true,
        session_timeout_ms: 30 * 60 * 1000, // 30 minutes
        batch_size: 10,
        batch_interval_ms: 2500,
        debug: false,
        persistence: 'localStorage',
        sanitize_inputs: true,
        visualizer: false,
    }

    // Auto-discover script tag config if present
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

    if (currentScript) {
        var scriptKey = currentScript.getAttribute('data-api-key')
        var scriptHost = currentScript.getAttribute('data-api-host')
        var scriptAuto = currentScript.getAttribute('data-autocapture')
        var scriptDebug = currentScript.getAttribute('data-debug')
        var scriptVis = currentScript.getAttribute('data-visualizer')

        if (scriptKey) config.api_key = scriptKey
        if (scriptHost) config.api_host = scriptHost.replace(/\/+$/, '')
        if (scriptAuto === 'false') config.autocapture = false
        if (scriptDebug === 'true') config.debug = true
        if (
            scriptVis === 'true' ||
            (window.location &&
                (window.location.search.indexOf('hotspot=visualize') !== -1 ||
                    window.location.search.indexOf('hotspot_hud=1') !== -1))
        ) {
            config.visualizer = true
        }

        // Infer host from script source if host is not specified
        if (!config.api_host && currentScript.src) {
            try {
                var url = new URL(currentScript.src)
                config.api_host = url.origin
            } catch (e) {
                config.api_host = window.location.origin
            }
        }
    }

    if (!config.api_host) {
        config.api_host = window.location.origin
    }

    // Helper: Generate UUID v4
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

    // Storage wrapper with fallback
    var storage = {
        get: function (key) {
            try {
                if (config.persistence === 'localStorage' && window.localStorage) {
                    return window.localStorage.getItem(key)
                }
            } catch (e) {}
            // Cookie fallback
            var name = key + '='
            var ca = document.cookie.split(';')
            for (var i = 0; i < ca.length; i++) {
                var c = ca[i].trim()
                if (c.indexOf(name) === 0) return decodeURIComponent(c.substring(name.length, c.length))
            }
            return null
        },
        set: function (key, value) {
            try {
                if (config.persistence === 'localStorage' && window.localStorage) {
                    window.localStorage.setItem(key, value)
                    return
                }
            } catch (e) {}
            var expires = '; max-age=' + 365 * 24 * 60 * 60 + '; path=/'
            document.cookie = key + '=' + encodeURIComponent(value) + expires + '; SameSite=Lax'
        },
        remove: function (key) {
            try {
                if (window.localStorage) window.localStorage.removeItem(key)
            } catch (e) {}
            document.cookie = key + '=; max-age=0; path=/'
        },
    }

    // Session Management
    function getDistinctId() {
        var id = storage.get(DISTINCT_ID_KEY)
        if (!id) {
            id = uuidv4()
            storage.set(DISTINCT_ID_KEY, id)
        }
        return id
    }

    function getDeviceId() {
        var devId = storage.get(DEVICE_ID_KEY)
        if (!devId) {
            devId = uuidv4()
            storage.set(DEVICE_ID_KEY, devId)
        }
        return devId
    }

    function getSessionId() {
        var now = Date.now()
        var lastTs = parseInt(storage.get(SESSION_TIMESTAMP_KEY) || '0', 10)
        var sessId = storage.get(SESSION_ID_KEY)

        if (!sessId || now - lastTs > config.session_timeout_ms) {
            sessId = uuidv4()
            storage.set(SESSION_ID_KEY, sessId)
        }
        storage.set(SESSION_TIMESTAMP_KEY, now.toString())
        return sessId
    }

    // Context & Super Properties
    function getDeviceProperties() {
        var width = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth || 0
        var height = window.innerHeight || document.documentElement.clientHeight || document.body.clientHeight || 0
        return {
            $lib: 'hotspot-web',
            $lib_version: '2.0.0',
            $screen_width: window.screen ? window.screen.width : width,
            $screen_height: window.screen ? window.screen.height : height,
            $viewport_width: width,
            $viewport_height: height,
            $current_url: window.location.href,
            $host: window.location.host,
            $pathname: window.location.pathname,
            $referrer: document.referrer || '',
            $title: document.title || '',
            $device_id: getDeviceId(),
            $session_id: getSessionId(),
            $browser_language: navigator.language || navigator.userLanguage || '',
            $timezone: Intl && Intl.DateTimeFormat ? Intl.DateTimeFormat().resolvedOptions().timeZone : '',
        }
    }

    function parseUtmParams() {
        var search = window.location.search
        if (!search) return {}
        var params = new URLSearchParams(search)
        var utm = {}
        var keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'ref', 'source']
        keys.forEach(function (k) {
            var val = params.get(k)
            if (val) utm[k] = val
        })
        return utm
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
        for (var k in props) {
            if (Object.prototype.hasOwnProperty.call(props, k)) {
                current[k] = props[k]
            }
        }
        storage.set(SUPER_PROPS_KEY, JSON.stringify(current))
    }

    // Event Queue & Network Transport
    var eventQueue = []
    var flushTimer = null

    function log() {
        if (config.debug && window.console && window.console.log) {
            var args = Array.prototype.slice.call(arguments)
            args.unshift('%c[Hotspot]%c', 'color:#ff5722;font-weight:bold;', 'color:inherit;')
            window.console.log.apply(window.console, args)
        }
    }

    function flushQueue(sync) {
        if (eventQueue.length === 0) return
        var batch = eventQueue.slice()
        eventQueue = []

        var endpoint = config.api_host + '/batch/'
        var payload = JSON.stringify({
            api_key: config.api_key,
            batch: batch,
            sent_at: new Date().toISOString(),
        })

        log('Flushing ' + batch.length + ' event(s) to ' + endpoint, batch)

        if (sync && navigator.sendBeacon) {
            var blob = new Blob([payload], { type: 'application/json' })
            var ok = navigator.sendBeacon(endpoint, blob)
            if (!ok && window.fetch) {
                try {
                    window.fetch(endpoint, {
                        method: 'POST',
                        body: payload,
                        headers: { 'Content-Type': 'application/json' },
                        keepalive: true,
                        mode: 'cors',
                    })
                } catch (e) {}
            }
            return
        }

        if (window.fetch) {
            window
                .fetch(endpoint, {
                    method: 'POST',
                    body: payload,
                    headers: { 'Content-Type': 'application/json' },
                    mode: 'cors',
                })
                .then(function (res) {
                    if (!res.ok) {
                        log('Server returned status ' + res.status)
                    }
                })
                .catch(function (err) {
                    log('Event delivery failed, buffering for retry', err)
                    // Re-enqueue events on network failure (up to 50 max)
                    if (eventQueue.length < 50) {
                        eventQueue = batch.concat(eventQueue)
                    }
                })
        }
    }

    function scheduleFlush() {
        if (flushTimer) clearTimeout(flushTimer)
        if (eventQueue.length >= config.batch_size) {
            flushQueue(false)
        } else {
            flushTimer = setTimeout(function () {
                flushQueue(false)
            }, config.batch_interval_ms)
        }
    }

    function recordEvent(eventName, properties) {
        if (storage.get(OPT_OUT_KEY) === 'true') {
            log('Tracking suppressed: user opted out')
            return
        }

        var distinctId = getDistinctId()
        var deviceProps = getDeviceProperties()
        var superProps = getSuperProperties()
        var utmProps = parseUtmParams()

        var mergedProps = Object.assign({}, deviceProps, superProps, utmProps, properties || {}, {
            distinct_id: distinctId,
        })

        var eventObj = {
            event: eventName,
            properties: mergedProps,
            timestamp: new Date().toISOString(),
        }

        eventQueue.push(eventObj)
        log('Captured event: ' + eventName, eventObj)

        // Trigger custom listener if registered
        if (hotspot._listeners && hotspot._listeners[eventName]) {
            hotspot._listeners[eventName].forEach(function (cb) {
                try {
                    cb(eventObj)
                } catch (e) {}
            })
        }

        scheduleFlush()
    }

    // Autocapture logic
    function getCssSelector(el) {
        if (!el || el.nodeType !== 1) return ''
        if (el.id) return '#' + el.id
        var selector = el.tagName.toLowerCase()
        if (el.className && typeof el.className === 'string') {
            var classes = el.className
                .trim()
                .split(/\s+/)
                .filter(function (c) {
                    return c.length > 0 && c.indexOf(':') === -1
                })
                .slice(0, 3)
            if (classes.length) selector += '.' + classes.join('.')
        }
        return selector
    }

    function getElementText(el) {
        if (!el) return ''
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            if (el.type === 'password' || config.sanitize_inputs) return ''
            return (el.value || '').substring(0, 50)
        }
        var text = el.innerText || el.textContent || ''
        return text.trim().substring(0, 80)
    }

    function isPotentiallyDeadClick(el) {
        if (!el || el.nodeType !== 1) return false
        var interactiveTags = ['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'LABEL', 'OPTION']
        var curr = el
        for (var i = 0; i < 4; i++) {
            if (!curr || curr === document.body || curr === document.documentElement) break
            if (interactiveTags.indexOf(curr.tagName) !== -1) return false
            var role = curr.getAttribute ? curr.getAttribute('role') : null
            if (role === 'button' || role === 'link' || role === 'tab' || role === 'menuitem') return false
            if (curr.onclick || (curr.getAttribute && curr.getAttribute('onclick'))) return false
            if (curr.tabIndex >= 0) return false
            try {
                var cursor = window.getComputedStyle ? window.getComputedStyle(curr).cursor : ''
                if (cursor === 'pointer') return false
            } catch (e) {}
            curr = curr.parentElement
        }
        return true
    }

    // Rage-click detection state
    var clickHistory = []

    function setupAutocapture() {
        if (!config.autocapture) return

        // Click & Heatmap Tracking
        document.addEventListener(
            'click',
            function (evt) {
                var target = evt.target
                if (!target || target.nodeType !== 1) return

                var clickX = evt.clientX || 0
                var clickY = evt.clientY || 0
                var pageX = evt.pageX || clickX + (window.scrollX || document.documentElement.scrollLeft || 0)
                var pageY = evt.pageY || clickY + (window.scrollY || document.documentElement.scrollTop || 0)

                var winWidth = window.innerWidth || 1
                var winHeight = window.innerHeight || 1
                var xRatio = +(clickX / winWidth).toFixed(4)
                var yRatio = +(clickY / winHeight).toFixed(4)

                var rect = target.getBoundingClientRect ? target.getBoundingClientRect() : null
                var elRelX = rect ? Math.round(clickX - rect.left) : 0
                var elRelY = rect ? Math.round(clickY - rect.top) : 0

                // Detect Rage Click (3+ clicks within 1.2s within 40px radius)
                var now = Date.now()
                clickHistory.push({ time: now, x: pageX, y: pageY })
                clickHistory = clickHistory.filter(function (c) {
                    return now - c.time < 1200
                })

                var isRageClick = false
                if (clickHistory.length >= 3) {
                    var clustered = clickHistory.filter(function (c) {
                        return Math.abs(c.x - pageX) < 40 && Math.abs(c.y - pageY) < 40
                    })
                    if (clustered.length >= 3) {
                        isRageClick = true
                        clickHistory = [] // reset once detected
                    }
                }

                var isDeadClick = isPotentiallyDeadClick(target)

                // Autocapture payload
                var clickProps = {
                    $event_type: 'click',
                    $el_tag_name: target.tagName.toLowerCase(),
                    $el_selector: getCssSelector(target),
                    $el_text: getElementText(target),
                    $el_id: target.id || '',
                    $el_class: typeof target.className === 'string' ? target.className : '',
                    $el_href: target.href || (target.closest && target.closest('a') ? target.closest('a').href : ''),
                    $is_dead_click: isDeadClick,
                    // Heatmap coordinates
                    $heatmap_x: Math.round(pageX),
                    $heatmap_y: Math.round(pageY),
                    $client_x: clickX,
                    $client_y: clickY,
                    $x_ratio: xRatio,
                    $y_ratio: yRatio,
                    $el_relative_x: elRelX,
                    $el_relative_y: elRelY,
                }

                recordEvent('$autocapture', clickProps)

                if (isRageClick) {
                    recordEvent('$rageclick', clickProps)
                }

                // Feed into in-browser visualizer
                if (typeof visualizer !== 'undefined') {
                    visualizer.recordClick({
                        x: Math.round(pageX),
                        y: Math.round(pageY),
                        clientX: clickX,
                        clientY: clickY,
                        time: now,
                        selector: clickProps.$el_selector,
                        text: clickProps.$el_text,
                        isRage: isRageClick,
                        isDead: isDeadClick,
                    })
                }
            },
            true
        )

        // Form submissions
        document.addEventListener(
            'submit',
            function (evt) {
                var form = evt.target
                if (!form || form.tagName !== 'FORM') return
                recordEvent('$form_submit', {
                    $form_id: form.id || '',
                    $form_name: form.name || '',
                    $form_action: form.action || '',
                    $form_method: (form.method || 'GET').toUpperCase(),
                })
            },
            true
        )
    }

    // Pageview & Pageleave Tracking
    var pageStartTime = Date.now()
    var maxScrollDepth = 0

    function updateScrollDepth() {
        var h = document.documentElement
        var b = document.body
        var st = 'scrollTop'
        var sh = 'scrollHeight'
        var percent = Math.round(((h[st] || b[st]) / ((h[sh] || b[sh]) - h.clientHeight)) * 100) || 0
        if (percent > maxScrollDepth) {
            maxScrollDepth = Math.min(100, Math.max(0, percent))
        }
    }

    window.addEventListener('scroll', updateScrollDepth, { passive: true })

    function capturePageview() {
        pageStartTime = Date.now()
        maxScrollDepth = 0
        recordEvent('$pageview', {
            $pathname: window.location.pathname,
            $title: document.title,
            $href: window.location.href,
        })
    }

    function capturePageleave() {
        if (!config.capture_pageleave) return
        var durationSec = Math.round((Date.now() - pageStartTime) / 1000)
        recordEvent('$pageleave', {
            $duration_seconds: durationSec,
            $scroll_depth_percent: maxScrollDepth,
        })
        flushQueue(true)
    }

    // Single Page App (SPA) URL changes
    function setupHistoryListeners() {
        var oldPushState = window.history.pushState
        if (oldPushState) {
            window.history.pushState = function () {
                capturePageleave()
                var ret = oldPushState.apply(this, arguments)
                setTimeout(capturePageview, 50)
                return ret
            }
        }
        var oldReplaceState = window.history.replaceState
        if (oldReplaceState) {
            window.history.replaceState = function () {
                capturePageleave()
                var ret = oldReplaceState.apply(this, arguments)
                setTimeout(capturePageview, 50)
                return ret
            }
        }
        window.addEventListener('popstate', function () {
            capturePageleave()
            setTimeout(capturePageview, 50)
        })
        window.addEventListener('hashchange', function () {
            capturePageleave()
            setTimeout(capturePageview, 50)
        })
    }

    // Error Autocapture
    function setupErrorCapture() {
        if (!config.capture_errors) return

        window.addEventListener('error', function (evt) {
            recordEvent('$exception', {
                $exception_message: evt.message || 'Unknown error',
                $exception_filename: evt.filename || '',
                $exception_lineno: evt.lineno || 0,
                $exception_colno: evt.colno || 0,
                $exception_type: evt.error ? evt.error.name : 'Error',
                $exception_stack: evt.error ? evt.error.stack : '',
            })
        })

        window.addEventListener('unhandledrejection', function (evt) {
            var reason = evt.reason || {}
            recordEvent('$exception', {
                $exception_message: reason.message || String(reason),
                $exception_type: 'UnhandledPromiseRejection',
                $exception_stack: reason.stack || '',
            })
        })
    }

    // ==========================================
    // In-Browser Heatmap Visualizer (Option + H)
    // ==========================================
    var visualizer = (function () {
        var clicks = []
        var isVisible = false
        var filterMode = 'all' // 'all', 'rage', 'dead'
        var canvas = null
        var ctx = null
        var hudContainer = null
        var hudPill = null
        var hudExpanded = null
        var animFrame = null

        try {
            var saved = sessionStorage.getItem('__hotspot_vis_clicks')
            if (saved) clicks = JSON.parse(saved)
        } catch (e) {}

        function persistClicks() {
            try {
                if (clicks.length > 250) clicks = clicks.slice(-250)
                sessionStorage.setItem('__hotspot_vis_clicks', JSON.stringify(clicks))
            } catch (e) {}
        }

        function createOverlay() {
            if (canvas || !document.body) return
            canvas = document.createElement('canvas')
            canvas.id = '__hotspot_vis_canvas'
            canvas.style.cssText =
                'position:fixed;top:0;left:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483640;display:none;'
            document.body.appendChild(canvas)
            ctx = canvas.getContext('2d')

            window.addEventListener('resize', requestRender)
            window.addEventListener('scroll', requestRender, { passive: true })
        }

        function createHud() {
            if (hudContainer || !document.body) return
            hudContainer = document.createElement('div')
            hudContainer.id = '__hotspot_hud_root'
            hudContainer.style.cssText =
                'position:fixed;bottom:20px;right:20px;z-index:2147483645;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#fff;'

            hudPill = document.createElement('div')
            hudPill.id = '__hotspot_hud_pill'
            hudPill.style.cssText =
                'display:flex;align-items:center;gap:8px;background:#111113;border:1px solid rgba(255,255,255,0.12);border-radius:24px;padding:8px 14px;box-shadow:0 8px 30px rgba(0,0,0,0.8), 0 0 15px rgba(0,82,255,0.15);cursor:pointer;user-select:none;font-size:12px;font-weight:600;letter-spacing:0.02em;transition:all 0.2s;'
            hudPill.innerHTML =
                '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#0052FF;box-shadow:0 0 8px rgba(0,82,255,0.8);animation:hsPulse 1.8s infinite;"></span>' +
                '<span style="color:#FAFAFA;font-weight:700;">Hotspot</span>' +
                '<span style="color:#71717A;">|</span>' +
                '<span id="__hotspot_hud_pill_count" style="color:#A1A1AA;">0 Clicks</span>' +
                '<span style="background:rgba(0,82,255,0.12);color:#0052FF;padding:2px 6px;border-radius:6px;font-size:10px;margin-left:4px;border:1px solid rgba(0,82,255,0.3);">⌥H</span>'

            hudExpanded = document.createElement('div')
            hudExpanded.id = '__hotspot_hud_expanded'
            hudExpanded.style.cssText =
                'display:none;width:320px;background:#111113;border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:16px;box-shadow:0 20px 50px rgba(0,0,0,0.9), 0 0 25px rgba(0,0,0,0.5);backdrop-filter:blur(16px);'
            hudExpanded.innerHTML =
                '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.08);">' +
                '  <div style="display:flex;align-items:center;gap:8px;">' +
                '    <div style="width:8px;height:8px;border-radius:50%;background:#0052FF;box-shadow:0 0 8px rgba(0,82,255,0.8);"></div>' +
                '    <span style="font-weight:700;font-size:13px;letter-spacing:-0.2px;color:#FAFAFA;">Hotspot Visualizer</span>' +
                '  </div>' +
                '  <button id="__hotspot_hud_minimize" style="background:none;border:none;color:#71717A;cursor:pointer;font-size:16px;padding:2px 6px;border-radius:4px;">✕</button>' +
                '</div>' +
                '<div style="grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px;display:grid;">' +
                '  <div style="background:#18181B;border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:8px;text-align:center;">' +
                '    <div style="font-size:10px;color:#71717A;text-transform:uppercase;margin-bottom:2px;">Clicks</div>' +
                '    <div id="__hotspot_stat_total" style="font-size:16px;font-weight:700;color:#0052FF;">0</div>' +
                '  </div>' +
                '  <div style="background:#18181B;border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:8px;text-align:center;">' +
                '    <div style="font-size:10px;color:#71717A;text-transform:uppercase;margin-bottom:2px;">Rage</div>' +
                '    <div id="__hotspot_stat_rage" style="font-size:16px;font-weight:700;color:#EF4444;">0</div>' +
                '  </div>' +
                '  <div style="background:#18181B;border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:8px;text-align:center;">' +
                '    <div style="font-size:10px;color:#71717A;text-transform:uppercase;margin-bottom:2px;">Dead</div>' +
                '    <div id="__hotspot_stat_dead" style="font-size:16px;font-weight:700;color:#F59E0B;">0</div>' +
                '  </div>' +
                '</div>' +
                '<div style="display:flex;gap:4px;margin-bottom:14px;background:#18181B;padding:3px;border-radius:8px;border:1px solid rgba(255,255,255,0.06);">' +
                '  <button id="__hotspot_filter_all" style="flex:1;background:#FAFAFA;color:#09090B;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:700;cursor:pointer;">All Clicks</button>' +
                '  <button id="__hotspot_filter_rage" style="flex:1;background:none;color:#71717A;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:600;cursor:pointer;">Rage</button>' +
                '  <button id="__hotspot_filter_dead" style="flex:1;background:none;color:#71717A;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:600;cursor:pointer;">Dead</button>' +
                '</div>' +
                '<div style="display:flex;gap:8px;">' +
                '  <button id="__hotspot_action_clear" style="flex:1;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:#FAFAFA;border-radius:8px;padding:7px 0;font-size:11px;font-weight:600;cursor:pointer;">Clear</button>' +
                '  <a id="__hotspot_action_dash" href="' + config.api_host + '/dashboard/" target="_blank" style="flex:1;display:flex;align-items:center;justify-content:center;text-decoration:none;background:rgba(0,82,255,0.12);border:1px solid rgba(0,82,255,0.3);color:#0052FF;border-radius:8px;padding:7px 0;font-size:11px;font-weight:600;">Open Dashboard ↗</a>' +
                '</div>'

            hudContainer.appendChild(hudPill)
            hudContainer.appendChild(hudExpanded)
            document.body.appendChild(hudContainer)

            if (!document.getElementById('__hotspot_styles')) {
                var style = document.createElement('style')
                style.id = '__hotspot_styles'
                style.textContent =
                    '@keyframes hsPulse { 0% { opacity: 0.5; transform: scale(0.95); } 50% { opacity: 1; transform: scale(1.1); } 100% { opacity: 0.5; transform: scale(0.95); } }' +
                    '#__hotspot_hud_pill:hover { border-color:rgba(0,82,255,0.5); transform: translateY(-1px); }'
                document.head.appendChild(style)
            }

            hudPill.addEventListener('click', function () {
                hudPill.style.display = 'none'
                hudExpanded.style.display = 'block'
                if (!isVisible) toggleOverlay(true)
            })

            var btnMinimize = document.getElementById('__hotspot_hud_minimize')
            if (btnMinimize) {
                btnMinimize.addEventListener('click', function () {
                    hudExpanded.style.display = 'none'
                    hudPill.style.display = 'flex'
                })
            }

            var btnFilterAll = document.getElementById('__hotspot_filter_all')
            var btnFilterRage = document.getElementById('__hotspot_filter_rage')
            var btnFilterDead = document.getElementById('__hotspot_filter_dead')

            function setFilter(mode) {
                filterMode = mode
                if (btnFilterAll) btnFilterAll.style.cssText = mode === 'all' ? 'flex:1;background:#FAFAFA;color:#09090B;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:700;cursor:pointer;' : 'flex:1;background:none;color:#71717A;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:600;cursor:pointer;'
                if (btnFilterRage) btnFilterRage.style.cssText = mode === 'rage' ? 'flex:1;background:#EF4444;color:#fff;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:700;cursor:pointer;' : 'flex:1;background:none;color:#71717A;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:600;cursor:pointer;'
                if (btnFilterDead) btnFilterDead.style.cssText = mode === 'dead' ? 'flex:1;background:#F59E0B;color:#09090B;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:700;cursor:pointer;' : 'flex:1;background:none;color:#71717A;border:none;border-radius:6px;padding:6px 0;font-size:11px;font-weight:600;cursor:pointer;'
                requestRender()
            }

            if (btnFilterAll) btnFilterAll.addEventListener('click', function () { setFilter('all') })
            if (btnFilterRage) btnFilterRage.addEventListener('click', function () { setFilter('rage') })
            if (btnFilterDead) btnFilterDead.addEventListener('click', function () { setFilter('dead') })

            var btnClear = document.getElementById('__hotspot_action_clear')
            if (btnClear) {
                btnClear.addEventListener('click', function () {
                    clicks = []
                    persistClicks()
                    updateHudStats()
                    requestRender()
                })
            }

            updateHudStats()
        }

        function updateHudStats() {
            var total = clicks.length
            var rageCount = 0
            var deadCount = 0
            for (var i = 0; i < clicks.length; i++) {
                if (clicks[i].isRage) rageCount++
                if (clicks[i].isDead) deadCount++
            }

            var elPill = document.getElementById('__hotspot_hud_pill_count')
            if (elPill) elPill.textContent = total + ' Clicks'
            var elTotal = document.getElementById('__hotspot_stat_total')
            if (elTotal) elTotal.textContent = total
            var elRage = document.getElementById('__hotspot_stat_rage')
            if (elRage) elRage.textContent = rageCount
            var elDead = document.getElementById('__hotspot_stat_dead')
            if (elDead) elDead.textContent = deadCount
        }

        function toggleOverlay(forceState) {
            isVisible = typeof forceState === 'boolean' ? forceState : !isVisible
            createOverlay()
            createHud()
            if (canvas) {
                canvas.style.display = isVisible ? 'block' : 'none'
            }
            if (hudContainer) {
                hudContainer.style.display = 'block'
            }
            if (isVisible) {
                requestRender()
            }
        }

        function requestRender() {
            if (!isVisible || !canvas || !ctx) return
            if (animFrame) cancelAnimationFrame(animFrame)
            animFrame = requestAnimationFrame(renderCanvas)
        }

        function renderCanvas() {
            if (!canvas || !ctx) return
            var dpr = window.devicePixelRatio || 1
            var w = window.innerWidth
            var h = window.innerHeight

            canvas.width = w * dpr
            canvas.height = h * dpr
            ctx.scale(dpr, dpr)
            ctx.clearRect(0, 0, w, h)

            var scrollX = window.scrollX || document.documentElement.scrollLeft || 0
            var scrollY = window.scrollY || document.documentElement.scrollTop || 0

            for (var i = 0; i < clicks.length; i++) {
                var c = clicks[i]
                if (filterMode === 'rage' && !c.isRage) continue
                if (filterMode === 'dead' && !c.isDead) continue

                var vx = c.x - scrollX
                var vy = c.y - scrollY

                if (vx < -60 || vx > w + 60 || vy < -60 || vy > h + 60) continue

                if (c.isRage) {
                    var gradR = ctx.createRadialGradient(vx, vy, 0, vx, vy, 45)
                    gradR.addColorStop(0, 'rgba(239, 68, 68, 0.85)')
                    gradR.addColorStop(0.35, 'rgba(239, 68, 68, 0.4)')
                    gradR.addColorStop(1, 'rgba(239, 68, 68, 0)')
                    ctx.fillStyle = gradR
                    ctx.beginPath()
                    ctx.arc(vx, vy, 45, 0, Math.PI * 2)
                    ctx.fill()

                    ctx.strokeStyle = '#EF4444'
                    ctx.lineWidth = 1.5
                    ctx.beginPath()
                    ctx.arc(vx, vy, 24, 0, Math.PI * 2)
                    ctx.stroke()
                } else if (c.isDead) {
                    var gradD = ctx.createRadialGradient(vx, vy, 0, vx, vy, 35)
                    gradD.addColorStop(0, 'rgba(245, 158, 11, 0.85)')
                    gradD.addColorStop(0.4, 'rgba(245, 158, 11, 0.35)')
                    gradD.addColorStop(1, 'rgba(245, 158, 11, 0)')
                    ctx.fillStyle = gradD
                    ctx.beginPath()
                    ctx.arc(vx, vy, 35, 0, Math.PI * 2)
                    ctx.fill()

                    ctx.strokeStyle = '#F59E0B'
                    ctx.lineWidth = 1.5
                    ctx.beginPath()
                    ctx.moveTo(vx - 7, vy)
                    ctx.lineTo(vx + 7, vy)
                    ctx.moveTo(vx, vy - 7)
                    ctx.lineTo(vx, vy + 7)
                    ctx.stroke()
                } else {
                    var gradN = ctx.createRadialGradient(vx, vy, 0, vx, vy, 32)
                    gradN.addColorStop(0, 'rgba(0, 82, 255, 0.85)')
                    gradN.addColorStop(0.35, 'rgba(0, 82, 255, 0.35)')
                    gradN.addColorStop(1, 'rgba(0, 82, 255, 0)')
                    ctx.fillStyle = gradN
                    ctx.beginPath()
                    ctx.arc(vx, vy, 32, 0, Math.PI * 2)
                    ctx.fill()
                }
            }
        }

        return {
            init: function () {
                window.addEventListener('keydown', function (e) {
                    if ((e.altKey || (e.ctrlKey && e.shiftKey)) && (e.key === 'h' || e.key === 'H' || e.keyCode === 72)) {
                        toggleOverlay()
                    }
                })

                if (
                    config.visualizer ||
                    (window.location &&
                        (window.location.search.indexOf('hotspot=visualize') !== -1 ||
                            window.location.search.indexOf('hotspot_hud=1') !== -1))
                ) {
                    if (document.readyState === 'complete' || document.readyState === 'interactive') {
                        createOverlay()
                        createHud()
                        toggleOverlay(true)
                    } else {
                        document.addEventListener('DOMContentLoaded', function () {
                            createOverlay()
                            createHud()
                            toggleOverlay(true)
                        })
                    }
                }
            },
            recordClick: function (c) {
                clicks.push(c)
                persistClicks()
                updateHudStats()
                if (isVisible) requestRender()
            },
            toggle: function (force) {
                toggleOverlay(force)
            },
            show: function () {
                toggleOverlay(true)
            },
            hide: function () {
                toggleOverlay(false)
            },
            getClicks: function () {
                return clicks
            },
            clear: function () {
                clicks = []
                persistClicks()
                updateHudStats()
                requestRender()
            },
        }
    })()

    // Public API
    var hotspot = {
        _config: config,
        _listeners: {},
        visualize: {
            toggle: function (force) {
                visualizer.toggle(force)
            },
            show: function () {
                visualizer.show()
            },
            hide: function () {
                visualizer.hide()
            },
            clear: function () {
                visualizer.clear()
            },
            getClicks: function () {
                return visualizer.getClicks()
            },
        },

        init: function (apiKey, userConfig) {
            if (apiKey) config.api_key = apiKey
            if (userConfig && typeof userConfig === 'object') {
                for (var key in userConfig) {
                    if (Object.prototype.hasOwnProperty.call(userConfig, key)) {
                        config[key] = userConfig[key]
                    }
                }
            }
            log('Hotspot initialized with API Key: ' + config.api_key)
            if (config.capture_pageview) {
                capturePageview()
            }
        },

        capture: function (eventName, properties) {
            recordEvent(eventName, properties)
        },

        identify: function (distinctId, properties) {
            if (!distinctId) return
            var oldId = getDistinctId()
            storage.set(DISTINCT_ID_KEY, String(distinctId))
            recordEvent('$identify', {
                $anon_distinct_id: oldId,
                $identified_id: String(distinctId),
                $set: properties || {},
            })
            log('Identified user as: ' + distinctId)
        },

        setPersonProperties: function (properties) {
            recordEvent('$set', {
                $set: properties || {},
            })
        },

        group: function (groupType, groupKey, properties) {
            recordEvent('$groupidentify', {
                $group_type: groupType,
                $group_key: groupKey,
                $group_set: properties || {},
            })
        },

        register: function (properties) {
            setSuperProperties(properties)
        },

        reset: function () {
            storage.remove(DISTINCT_ID_KEY)
            storage.remove(SESSION_ID_KEY)
            storage.remove(SUPER_PROPS_KEY)
            log('User session reset')
        },

        optOut: function () {
            storage.set(OPT_OUT_KEY, 'true')
            eventQueue = []
            log('User opted out of tracking')
        },

        optIn: function () {
            storage.remove(OPT_OUT_KEY)
            log('User opted in to tracking')
        },

        hasOptedOut: function () {
            return storage.get(OPT_OUT_KEY) === 'true'
        },

        getDistinctId: function () {
            return getDistinctId()
        },

        getSessionId: function () {
            return getSessionId()
        },

        on: function (eventName, callback) {
            if (!this._listeners[eventName]) this._listeners[eventName] = []
            this._listeners[eventName].push(callback)
        },
    }

    // Attach to window
    window.hotspot = hotspot

    // Initialize lifecycle hooks
    setupAutocapture()
    setupHistoryListeners()
    setupErrorCapture()
    visualizer.init()

    window.addEventListener('beforeunload', capturePageleave)
    window.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
            capturePageleave()
        }
    })

    // If script had data-api-key, trigger first pageview automatically
    if (config.api_key && config.capture_pageview) {
        if (document.readyState === 'complete' || document.readyState === 'interactive') {
            capturePageview()
        } else {
            document.addEventListener('DOMContentLoaded', capturePageview)
        }
    }
})(window, document)
