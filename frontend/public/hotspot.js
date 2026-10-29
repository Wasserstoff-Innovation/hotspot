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

        if (scriptKey) config.api_key = scriptKey
        if (scriptHost) config.api_host = scriptHost.replace(/\/+$/, '')
        if (scriptAuto === 'false') config.autocapture = false
        if (scriptDebug === 'true') config.debug = true

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

                // Autocapture payload
                var clickProps = {
                    $event_type: 'click',
                    $el_tag_name: target.tagName.toLowerCase(),
                    $el_selector: getCssSelector(target),
                    $el_text: getElementText(target),
                    $el_id: target.id || '',
                    $el_class: typeof target.className === 'string' ? target.className : '',
                    $el_href: target.href || (target.closest && target.closest('a') ? target.closest('a').href : ''),
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

    // Public API
    var hotspot = {
        _config: config,
        _listeners: {},

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
