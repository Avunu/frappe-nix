#!/usr/bin/env node
// Prints the frappe.call options a desk page would send. A node tool with a shebang, which
// validate_copyright must leave on line 1; it names one of frappe's own whitelisted methods,
// which static_analysis must resolve outside a bench (pyproject.toml whitelists frappe.*).
const request = { method: "frappe.client.get_list", args: { doctype: "ToDo", limit_page_length: 1 } };

console.log(JSON.stringify(request));
