import frappe

frappe_version = int(frappe.__version__.split(".")[0])

app_name = "esign"
app_title = "eSign"
app_publisher = "Avunu LLC"
app_description = "Collect Electronic Signatures on Frappe Documents via Webforms"
app_email = "mail@avu.nu"
app_license = "MIT"

app_include_js = ["esign.desk.bundle.js", "esign.control.bundle.js"]
app_include_css = [
	"esign.control.bundle.css",
	"/assets/esign/dist/esign-fonts.css",
]
web_include_js = ["esign.web.bundle.js"]
# Names no file: the rename must leave it alone and report it.
web_include_css = ["esign.legacy.bundle.css"]

after_install = "esign.config.after_install"

additional_timeline_content = {
	"*": ["esign.esign.get_timeline_content"],
}

if frappe_version >= 16:
	extend_doctype_class = {
		"Web Form": "esign.esign.custom.web_form.EsignWebForm",
	}

jinja = {
	"methods": [
		"esign.esign.custom.web_form.get_esign_link",
	]
}
