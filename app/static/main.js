// global variables
var system_hosts;
var leases;
var config;
var hide_commented;
//var edit_log = [];

function compare(a, b) {
    if (a < b) { return -1; }
    else if (a > b) { return 1; }
    else { return 0; }
}

function to_safe(val) {
    if (val === undefined) { return ""; }
    //else if (typeof(val) == 'number') { return val; }
    //else { return String(val); }
    return val;
}

function compare_by_key(key, ascend = true) {
    return function (a, b) {
        var c = compare(to_safe(a[key]), to_safe(b[key]));
        return ascend ? c : -c;
    }
}

function match_host(host, cond) {
    for (var key in cond) {
        //console.log(key);
        if (host[key] != cond[key]) {
            return false;
        }
    }
    return true;
}

function modify_hosts(hosts, cond, apply) {
    for (var host of hosts) {
        if (match_host(host, cond)) {
            for (var key in apply) {
                host[key] = apply[key];
            }
        }
    }
}

$(function () {
    function add_static (e) {
        var tag_click = $(e.currentTarget);
        var num = tag_click.data('num');
        var host = leases[num-1];
        var new_host = {
            num: config.hosts.length + 1,
            valid: true,
            addr: host.addr,
            mac: [host.mac],
            extra: [],
            name: host.name,
            changed: true,
        };
        config.hosts.push(new_host);
        $('#save').removeClass('disabled');
        update_hosts('dhcp-hosts');
        tag_click.remove();
    }
    function add_ignore(e) {
        var tag_click = $(e.currentTarget);
        //update_hosts('dhcp-hosts');
        //var host = {
        //    commented: false,
        //    valid: true,
        //    num: config.hosts.length + 1,
        //};
        //config.hosts.push(host);
        //update_hosts('dhcp-hosts');
        tag_click.remove();
        $('#save').removeClass('disabled');
    }
    function delete_host(e) {
        var tag_click = $(e.currentTarget);
        var line_num = tag_click.data('line_num');
        var response = window.confirm('Are you sure to delete this host?');
        var change = {
            delete:true,
            changed:true,
        };
        if (response) {
            modify_hosts(config.hosts, {line_num:line_num}, change);
        }
        update_hosts('dhcp-hosts');
        $('#save').removeClass('disabled');
    }
    function on_change(e) {
        var tag_change = $(e.currentTarget);
        //var num = tag_change.data('num');
        var line_num = tag_change.data('line_num');
        console.log(line_num);
        var key = tag_change.data('key');
        var val = tag_change.val();
        //console.log(key);
        //console.log(val);
        //console.log(tag_change.prop("tagName"));
        if (tag_change.prop("tagName") == "TEXTAREA") {
            val = val.trim().split('\n');
        } else if (key == "valid") {
            val = tag_change.prop("checked");
        }
        var change = {};
        change[key] = val;
        change['changed'] = true;
        //modify_hosts(config.hosts, {num:num}, change)
        modify_hosts(config.hosts, {line_num:line_num}, change)
        //change.line = host.line;
        //change.action = "change";
        //change.line = line;
        //change.key = key;
        //change.value = val;
        //edit_log.push(change);
        //console.log(change);
        $('#save').removeClass('disabled');
        update_hosts('dhcp-hosts');
        return false;
    }
    function on_keydown(e) {
        var target = $(e.currentTarget);
        //console.log(target);
        //console.log("keydown");
        //console.log(e);
        //console.log(e.keyCode);
        switch(e.keyCode) {
            case 13: // enter
                var num_lines = target.val().split('\n').length;
                target.attr('rows', num_lines+1);
                break;
        }
        return true;
    }

    //function update_hosts(table_id, sort_key = null, ascend = true) {
    function update_hosts(table_id, sort_key = null, ascend = null) {
        var hosts;
        var tag_table = $('#' + table_id);
        //console.log(hosts_table);
        var tag_thead = tag_table.find('thead');
        //console.log(hosts_thead);
        var tag_tbody = tag_table.find('tbody');
        //console.log(hosts_tbody);
        tag_tbody.empty();
        switch (table_id) {
            case 'dhcp-hosts': hosts = config.hosts; break;
            case 'ignored-hosts': hosts = config.ignored_hosts; break;
            case 'system-hosts': hosts = system_hosts; break;
            case 'dhcp-leases': hosts = leases; break;
        }
        if (sort_key === null) {
            sort_key = tag_table.data('sort-key');
            ascend = tag_table.data('sort-ascend');
        }
        if (sort_key) {
            hosts.sort(compare_by_key(sort_key, ascend));
            tag_table.data('sort-key', sort_key);
            tag_table.data('sort-ascend', ascend);
        }
        for (var host of hosts) {
            //console.log(host);
            if (host.delete) {
                continue;
            }
            if (hide_commented && !host.valid) {
                continue;
            }
            var tag_tr = $('<tr>').appendTo(tag_tbody);
            if (host.valid === false) {
                tag_tr.addClass("table-secondary");
            }
            tag_thead.find('[data-key]').each(function (i, e) {
                var key = $(e).data('key');
                var editable = $(e).data('editable');
                //console.log(editable);
                //console.log(key);
                var val = host[key];
                var tag_td = $('<td class="text-center align-middle">').appendTo(tag_tr);
                if (key == 'control') {
                    var button_add_static = $('<a class="add-static btn btn-primary text-white">Add Static</a>');
                    button_add_static.data('num', host.num);
                    button_add_static.click(add_static);
                    var button_ignore = $('<a class="add-ignore btn btn-danger text-white">Ignore</a>');
                    button_ignore.data('num', host.num);
                    button_ignore.click(add_ignore);
                    tag_td.append(button_add_static);
                    tag_td.append('&nbsp;');
                    tag_td.append(button_ignore);
                } else if (key == 'delete') {
                    $('<a class="delete-host btn btn-danger text-white">Delete</a>')
                        .data('line_num', host.line_num)
                        .click(delete_host)
                        .appendTo(tag_td);
                } else if (Array.isArray(val)) {
                    //$('<td>').html(val.join('<br/>')).appendTo(tr);
                    if (editable && host.valid) {
                        //tag_td.html(val.join('<br/>')).appendTo(tr);
                        $('<textarea class="edit-host form-control">')
                            .attr('rows', val.length+1)
                            .val(val.join('\n'))
                            //.data('num', host.num)
                            .data('line_num', host.line_num)
                            .data('key', key)
                            .appendTo(tag_td);
                    } else {
                        tag_td.html(val.join('<br/>'));
                    }
                } else {
                    //$('<td>').text(val).appendTo(tr);
                    if (editable) {
                        if (typeof (val) == 'boolean') {
                            //console.log(key);
                            var tag_check = $('<input class="edit-host" type="checkbox">').appendTo(tag_td);
                            //tag_check.data('num', host.num);
                            tag_check.data('line_num', host.line_num);
                            tag_check.data('key', key);
                            if (val) {
                                tag_check.prop('checked', true);
                            }
                            //tag_check.change(function (e) {
                            //    var tag_check = $(e.currentTarget);
                            //    var checked = tag_check.prop('checked');
                            //    //var num = tag_check.data('num');
                            //    var line = tag_check.data('line');
                            //    var new_config = {
                            //        valid: checked,
                            //        commented: !checked,
                            //    };
                            //    modify_hosts(hosts, {line:line}, new_config);
                            //    update_hosts(table_id, sort_key, ascend)
                            //});
                        } else {
                            if (host.valid) {
                                $('<input class="edit-host form-control">')
                                    .val(val)
                                    //.data('num', host.num)
                                    .data('line_num', host.line_num)
                                    .data('key', key)
                                    .appendTo(tag_td);
                            } else {
                                tag_td.text(val).appendTo(tag_tr);
                            }
                        }
                    } else {
                        tag_td.text(val).appendTo(tag_tr);
                    }
                }
            });
        }
    }
    $('a').css('cursor', 'pointer');
    $('.sort-button').click(function (e) {
        var link = $(e.currentTarget);
        var table = link.closest('table');
        var table_id = table.attr('id');
        //console.log(table);
        //console.log(table.attr('id'));
        var key = link.data('key');
        var ascend = link.data('ascend');
        if (ascend === undefined) {
            ascend = true;
        }
        //$('.sort-button').data('ascend', true);
        table.find('.sort-button').data('ascend', true);
        table.find('.sort-order').remove();
        link.data('ascend', !ascend);
        if (ascend) {
            link.append(' <span class="sort-order">(↑)</span>');
        } else {
            link.append(' <span class="sort-order">(↓)</span>');
        }
        update_hosts(table_id, key, ascend);
    });
    $('#hide-commented').change(function (e) {
        var tag_check = $(e.currentTarget);
        var checked = tag_check.prop('checked');
        console.log(checked);
        hide_commented = checked;
        update_hosts('dhcp-hosts');
    });
    $('#add-host').click(function (e) {
        var tag_click = $(e.currentTarget);
        //update_hosts('dhcp-hosts');
        var host = {
            //commented: false,
            valid: true,
            num: config.hosts.length + 1,
            mac: [],
            extra: [],
        };
        config.hosts.push(host);
        update_hosts('dhcp-hosts');
    });
    $('#save').click(function (e) {
        var tag_click = $(e.currentTarget);
        var data = {
            hosts: config.hosts,
        };
        $.ajax({
            type: "POST",
            url: "/api/save",
            data: JSON.stringify(data),
            contentType: "application/json;charset=UTF-8",
            success: function(data) {
                console.log(data);
                //window.location.reload();
            },
        });
        for (host of config.hosts) {
            delete host.changed;
        }
        tag_click.addClass("disabled");
    });
    $(document).on('change', '.edit-host', on_change);
    $(document).on('keydown', 'textarea', on_keydown);
    update_hosts('dhcp-hosts');
    update_hosts('ignored-hosts');
    update_hosts('system-hosts');
    update_hosts('dhcp-leases');
});
