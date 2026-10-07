import frappe
from frappe.website.doctype.web_form.web_form import WebForm

from esign.esign import get_timeline_content


class EsignWebForm(WebForm):
	def esign_enabled(self):
		return bool(get_timeline_content(self.doctype, self.name) is not None)


@frappe.whitelist(allow_guest=True)
def accept(web_form, docname):
	return frappe.render_template("esign/templates/esign.html", {"docname": docname})


def get_esign_link(doc):
	return f"/api/method/esign.esign.custom.web_form.accept?docname={doc.name}"
