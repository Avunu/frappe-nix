app_name = "spa_app"
app_title = "SPA App"
app_publisher = "Example Org"
app_description = "The app frappe-nix's asset self-tests build: Vite bundles at the root and in portal/"
app_email = "apps@example.org"
app_license = "MIT"

required_apps = []

# Vite bundles, by the keys scripts/vite-register.mjs writes to assets.json.
app_include_js = ["spa.bundle.js"]
app_include_css = ["spa.bundle.css"]
