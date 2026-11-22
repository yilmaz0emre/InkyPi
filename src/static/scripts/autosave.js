// Autosave for settings forms.
// - Plugin page, saved instance: changes are PUT to the instance shortly after the user edits the form.
// - Plugin page, new (unsaved) plugin: the form is stored on the device as a per-plugin draft and
//   restored on the next visit (from any browser), until it is added to a playlist.
// - Device settings page: the form is POSTed to save_settings.
// Hidden inputs that scripts update (frame, dashboard layout) fire no events, so once the user has
// interacted the form is also compared against the last saved snapshot on a timer.

const Autosave = (function () {
    const DEBOUNCE_MS = 1000;
    const POLL_MS = 1500;

    // Same shape as parse_form() on the server: keys ending in [] become lists. Files are left out.
    function formToSettings(form) {
        const settings = {};
        for (const [key, value] of new FormData(form).entries()) {
            if (value instanceof File) continue;
            if (key.endsWith('[]')) {
                (settings[key] = settings[key] || []).push(value);
            } else {
                settings[key] = value;
            }
        }
        return settings;
    }

    function settingsToFormData(settings) {
        const data = new FormData();
        Object.entries(settings).forEach(([key, value]) => {
            (Array.isArray(value) ? value : [value]).forEach(v => data.append(key, v));
        });
        return data;
    }

    function fileSignature(form, extraFiles) {
        const names = [];
        for (const [key, value] of new FormData(form).entries()) {
            if (value instanceof File && value.name) names.push(`${key}=${value.name}:${value.size}`);
        }
        const extra = extraFiles() || {};
        Object.keys(extra).forEach(key => {
            (extra[key] || []).forEach(file => names.push(`${key}=${file.name}:${file.size}`));
        });
        return names.join('|');
    }

    /**
     * options: form, status (elements), url, method, messages {idle, saving, saved, failed, fileNeedsSave},
     * extraFiles() -> {key: [File]}, onSaved(response, settings) optional.
     */
    function attach(options) {
        const { form, status, url, method, messages } = options;
        if (!form || !status) return null;
        const extraFiles = options.extraFiles || (() => ({}));
        let interacted = false;
        let baseline = null;
        let baselineFiles = '';
        let timer = null;
        let saving = false;

        const snapshot = () => JSON.stringify(formToSettings(form));
        const setStatus = (text, state) => {
            status.textContent = text;
            status.dataset.state = state || '';
        };

        async function save(keepalive) {
            if (saving && !keepalive) return;
            const current = snapshot();
            if (current === baseline) return;
            if (fileSignature(form, extraFiles) !== baselineFiles) {
                setStatus(messages.fileNeedsSave, 'pending');
                return;
            }
            const body = settingsToFormData(JSON.parse(current));
            if (keepalive) {
                fetch(url, { method, body, keepalive: true }).catch(() => {});
                return;
            }
            saving = true;
            setStatus(messages.saving, 'saving');
            try {
                const response = await fetch(url, { method, body });
                const result = await response.json().catch(() => ({}));
                if (!response.ok) {
                    // a validation message from the server is more useful than a generic one
                    setStatus(result.error || messages.failed, 'error');
                    baseline = current;  // do not retry the same invalid input in a loop
                    return;
                }
                baseline = current;
                setStatus(messages.saved, 'saved');
                if (options.onSaved) options.onSaved(result, JSON.parse(current));
            } catch (e) {
                console.error('Autosave failed', e);
                setStatus(messages.failed, 'error');  // network error: the poll retries
            } finally {
                saving = false;
            }
        }

        function schedule() {
            if (!interacted || baseline === null) return;
            clearTimeout(timer);
            timer = setTimeout(() => save(false), DEBOUNCE_MS);
        }

        const markInteracted = () => { interacted = true; schedule(); };
        ['input', 'change', 'pointerup', 'keyup'].forEach(type => form.addEventListener(type, markInteracted));
        // select2 fires jQuery-only change events
        if (window.jQuery) window.jQuery(form).on('change', markInteracted);

        setInterval(() => {
            if (interacted && baseline !== null && snapshot() !== baseline) schedule();
        }, POLL_MS);

        window.addEventListener('pagehide', () => {
            if (!interacted || baseline === null) return;
            clearTimeout(timer);
            save(true);
        });

        // Take the baseline once page scripts have filled the form in.
        const start = () => setTimeout(() => {
            // an edit made while the page was still loading must not become the baseline
            baseline = interacted ? '' : snapshot();
            baselineFiles = fileSignature(form, extraFiles);
            if (messages.idle) setStatus(messages.idle, '');
            if (options.onReady) options.onReady(setStatus);
            if (interacted) schedule();
        }, 500);
        if (document.readyState === 'complete') start(); else window.addEventListener('load', start);

        return {
            // after a manual save the current form is the saved state
            markSaved() {
                clearTimeout(timer);
                baseline = snapshot();
                baselineFiles = fileSignature(form, extraFiles);
                setStatus(messages.saved, 'saved');
            },
            // stop treating the current form as unsaved (e.g. after it was added to a playlist and reset)
            reset() {
                clearTimeout(timer);
                interacted = false;
                baseline = snapshot();
                setStatus('', '');
            },
        };
    }

    return { attach };
})();
