// Autosave for the plugin settings page.
// - Saved instance: changes are PUT to the instance shortly after the user edits the form.
// - New (unsaved) plugin: the form is kept as a per-plugin draft in localStorage and restored on the
//   next visit, until it is added to a playlist.
// Hidden inputs that scripts update (frame, dashboard layout) fire no events, so the form is compared
// against the last saved snapshot on a timer as well as on input events.

const PluginAutosave = (function () {
    const DEBOUNCE_MS = 1000;
    const POLL_MS = 1500;

    function draftKey(pluginId) {
        return `inkypi-draft:${pluginId}`;
    }

    // Read a stored draft; returns null when there is none or storage is unavailable.
    function loadDraft(pluginId) {
        try {
            const raw = localStorage.getItem(draftKey(pluginId));
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function clearDraft(pluginId) {
        try {
            localStorage.removeItem(draftKey(pluginId));
        } catch (e) { /* storage unavailable */ }
    }

    // Same shape as parse_form() on the server: keys ending in [] become lists.
    function formToSettings(form) {
        const settings = {};
        const data = new FormData(form);
        for (const [key, value] of data.entries()) {
            if (value instanceof File || key === 'plugin_id') continue;
            if (key.endsWith('[]')) {
                (settings[key] = settings[key] || []).push(value);
            } else {
                settings[key] = value;
            }
        }
        return settings;
    }

    function fileSignature(form, uploadedFiles) {
        const names = [];
        for (const [key, value] of new FormData(form).entries()) {
            if (value instanceof File && value.name) names.push(`${key}=${value.name}:${value.size}`);
        }
        Object.keys(uploadedFiles() || {}).forEach(key => {
            (uploadedFiles()[key] || []).forEach(file => names.push(`${key}=${file.name}:${file.size}`));
        });
        return names.join('|');
    }

    function init(options) {
        const form = document.getElementById('settingsForm');
        const status = document.getElementById('autosaveStatus');
        if (!form || !status) return null;

        const { pluginId, instanceName, saveUrl, messages } = options;
        const uploadedFiles = options.uploadedFiles || (() => ({}));
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

        async function saveInstance(keepalive) {
            if (saving && !keepalive) return;
            const current = snapshot();
            if (current === baseline) return;
            if (fileSignature(form, uploadedFiles) !== baselineFiles) {
                setStatus(messages.fileNeedsSave, 'pending');
                return;
            }
            const data = new FormData();
            Object.entries(formToSettings(form)).forEach(([key, value]) => {
                (Array.isArray(value) ? value : [value]).forEach(v => data.append(key, v));
            });
            data.append('plugin_id', pluginId);
            if (keepalive) {
                fetch(saveUrl, { method: 'PUT', body: data, keepalive: true }).catch(() => {});
                return;
            }
            saving = true;
            setStatus(messages.saving, 'saving');
            try {
                const response = await fetch(saveUrl, { method: 'PUT', body: data });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                baseline = current;
                setStatus(messages.saved, 'saved');
            } catch (e) {
                console.error('Autosave failed', e);
                setStatus(messages.failed, 'error');
            } finally {
                saving = false;
            }
        }

        function saveDraft() {
            const current = snapshot();
            if (current === baseline) return;
            try {
                localStorage.setItem(draftKey(pluginId), current);
                baseline = current;
                setStatus(messages.draftSaved, 'saved');
            } catch (e) { /* storage unavailable: nothing to do */ }
        }

        const flush = instanceName ? () => saveInstance(false) : saveDraft;

        function schedule() {
            if (!interacted || baseline === null) return;
            clearTimeout(timer);
            timer = setTimeout(flush, DEBOUNCE_MS);
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
            if (instanceName) saveInstance(true); else saveDraft();
        });

        // Take the baseline once plugin scripts have filled the form in.
        window.addEventListener('load', () => setTimeout(() => {
            // an edit made while the page was still loading must not become the baseline
            baseline = interacted ? '' : snapshot();
            baselineFiles = fileSignature(form, uploadedFiles);
            if (interacted) schedule();
            if (instanceName) {
                setStatus(messages.autosaveOn, '');
            } else if (options.draftRestored) {
                status.textContent = messages.draftRestored + ' ';
                const clear = document.createElement('a');
                clear.href = '#';
                clear.textContent = messages.clearDraft;
                clear.addEventListener('click', event => {
                    event.preventDefault();
                    clearDraft(pluginId);
                    interacted = false;
                    window.location.reload();
                });
                status.appendChild(clear);
            }
        }, 500));

        return {
            // after a manual save/add the current form is the saved state
            markSaved() {
                clearTimeout(timer);
                baseline = snapshot();
                baselineFiles = fileSignature(form, uploadedFiles);
                if (instanceName) setStatus(messages.saved, 'saved');
            },
            // after "Add to Playlist" the draft is done with
            discardDraft() {
                clearTimeout(timer);
                clearDraft(pluginId);
                interacted = false;
                baseline = snapshot();
                setStatus('', '');
            },
        };
    }

    return { init, loadDraft };
})();
