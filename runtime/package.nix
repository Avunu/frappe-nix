{
  lib,
  buildPythonPackage,
  hatchling,
  python-socketio,
  python-engineio,
  uvicorn,
  a2wsgi,
  httpx,
  watchdog,
}:

buildPythonPackage {
  pname = "frappe-runtime";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./README.md
      ./src
    ];
  };

  build-system = [ hatchling ];

  # Only what Frappe does not already bring. frappe, redis, werkzeug, rq and
  # watchdog are all Frappe's own dependencies and come from the bench
  # environment this is installed into; declaring them here would either
  # duplicate them or, for frappe, be unresolvable.
  dependencies = [
    python-socketio
    python-engineio
    uvicorn
    a2wsgi
    httpx
    watchdog
  ];

  # `import frappe_runtime` reaches frappe (the compat shim, and auth.py), which
  # is not in nixpkgs and is not a declared dependency. The real test run happens
  # against a bench — see scripts/run-tests.sh.
  pythonImportsCheck = [ ];
  doCheck = false;

  meta = {
    description = "Frappe's Python runtime: Socket.IO realtime server, ASGI adapter, and unified process runner";
    license = lib.licenses.mit;
    mainProgram = "frappe-runtime";
  };
}
