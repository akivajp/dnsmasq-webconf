// Full-page text editor for the config file (#18).
// config_text / config_digest / read_only are embedded beforehand by the
// page's inline script. As in main.js, initialize only when undefined so
// the embedded values are not overwritten
if (typeof config_text === 'undefined') { config_text = ''; }
if (typeof config_digest === 'undefined') { config_digest = ''; }
if (typeof read_only === 'undefined') { read_only = false; }

$(function () {
    var tag_text = $('#config-text');
    var tag_save = $('#save-text');
    // Content as last loaded or saved; the editor is dirty when it differs
    var saved_text;
    // Flag to suppress the leave-page warning when the user chose to reload
    var leaving = false;

    // Set the default value (not just .val()) so the text is part of the DOM
    tag_text.prop('defaultValue', config_text);
    // Read it back: the textarea normalizes line breaks, so compare against that
    saved_text = tag_text.val();

    function is_dirty() {
        return tag_text.val() !== saved_text;
    }

    function update_save_button() {
        tag_save.toggleClass('disabled', !is_dirty());
    }

    // Show the save result. Values from the server are inserted as text nodes.
    // actions: optional list of buttons/links appended below the message
    function show_status(level, title, details, actions) {
        var tag_alert = $('<div class="alert">')
            .addClass('alert-' + level)
            .append($('<strong>').text(title));
        if (details && details.length) {
            var tag_list = $('<ul class="mb-0 mt-2">');
            details.forEach(function (line) {
                $('<li>').text(line).appendTo(tag_list);
            });
            tag_alert.append(tag_list);
        }
        if (actions && actions.length) {
            var tag_actions = $('<div class="mt-2">').appendTo(tag_alert);
            actions.forEach(function (action) {
                tag_actions.append(action).append(' ');
            });
        }
        $('#save-status').empty().append(tag_alert);
    }

    function save(force) {
        if (read_only) { return; }
        var text = tag_text.val();
        tag_save.addClass('disabled');
        $.ajax({
            type: 'POST',
            url: '/api/save-text',
            data: JSON.stringify({text: text, digest: config_digest, force: force}),
            contentType: 'application/json;charset=UTF-8',
            success: function (result) {
                if (result.status === 'CONFLICT') {
                    // Someone else wrote the file since it was loaded. Keep the
                    // edits and let the user decide whether to overwrite
                    var button_force = $('<a class="btn btn-danger text-white">Overwrite Anyway</a>')
                        .css('cursor', 'pointer')
                        .click(function () { save(true); });
                    var button_reload = $('<a class="btn btn-outline-secondary">Reload and Discard My Edits</a>')
                        .css('cursor', 'pointer')
                        .click(function () {
                            leaving = true;
                            window.location.reload();
                        });
                    show_status(
                        'warning',
                        'The file was changed on disk after you opened it. Nothing was saved.',
                        ['Overwriting replaces those changes with your text.'],
                        [button_force, button_reload]
                    );
                    update_save_button();
                    return;
                }
                if (result.status === 'REJECTED') {
                    // Validation (dnsmasq --test etc.) failed; the file was not modified
                    var output = (result.validation && result.validation.output) || '';
                    show_status(
                        'danger',
                        'Pre-save validation failed — the file was not modified:',
                        [output, 'Fix the text and save again.']
                    );
                    update_save_button();
                    return;
                }
                // Saved: the new content and digest become the base for the next save
                config_digest = result.digest || '';
                saved_text = text;
                var details = [];
                if (result.reload && result.reload.returncode !== 0) {
                    details.push('Reload command failed: ' + (result.reload.output || ''));
                }
                show_status(
                    details.length ? 'warning' : 'success',
                    result.changed ? 'Saved.' : 'No changes to save.',
                    details
                );
                update_save_button();
            },
            error: function (xhr) {
                show_status(
                    'danger',
                    'Save failed (HTTP ' + xhr.status + ')',
                    [xhr.responseText ? String(xhr.responseText).slice(0, 300) : '']
                );
                update_save_button();
            },
        });
    }

    $('a').css('cursor', 'pointer');
    tag_text.on('input', update_save_button);
    tag_save.click(function () {
        if (!tag_save.hasClass('disabled')) { save(false); }
    });
    // Ctrl+S / Cmd+S saves instead of opening the browser's "Save page" dialog
    $(document).on('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            if (is_dirty()) { save(false); }
        }
    });
    // Warn on leaving the page only when there are unsaved edits
    window.addEventListener('beforeunload', function (e) {
        if (leaving || !is_dirty()) { return; }
        e.preventDefault();
        e.returnValue = '';
    });
});
