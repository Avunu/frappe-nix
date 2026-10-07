// The desk half: calls the whitelisted method by its dotted path.
frappe.provide("esign");
esign.accept = (docname) => frappe.call({ method: "esign.esign.custom.web_form.accept", args: { docname } });
// localStorage keys keep their name: renaming one would forget every user's setting.
localStorage.getItem("esign_signature_pad");
