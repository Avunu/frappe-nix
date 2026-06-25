environment = {
    DEV_SERVER = "1";
    FRAPPE_ENV_TYPE = "development";
    FRAPPE_STREAM_LOGGING = "1";
    FRAPPE_TUNE_GC = "1";
    LIVE_RELOAD = "1";
    NO_SERVICE_RESTART = "1";

    USE_PROFILER = "";
    USE_PROXY = "";
    NO_STATICS = "";

    FRAPPE_DB_HOST = "127.0.0.1";
    FRAPPE_DB_PORT = "3306";
    FRAPPE_DB_TYPE = "mariadb";

    FRAPPE_REDIS_CACHE = "redis://localhost:13000";
    FRAPPE_REDIS_QUEUE = "redis://localhost:13000";
    FRAPPE_REDIS_SOCKETIO = "redis://localhost:13000";

    FRAPPE_WEBSERVER_PORT = "8000";
    FRAPPE_SOCKETIO_PORT = "9000";
    FRAPPE_FILE_WATCHER_PORT = "6787";

    MAILPIT_SMTP_PORT = "1025";
    MAILPIT_HTTP_PORT = "8025";

    FRAPPE_DB_SOCKET = config.env.DEVENV_RUNTIME + "/mysql.sock";
    FRAPPE_SOCKETS_DIR = config.env.DEVENV_STATE + "/sockets";
    FRAPPE_WEB_SOCKET = config.env.DEVENV_STATE + "/sockets/frappe.sock";

    FRAPPE_BENCH_ROOT = config.devenv.root;
    SITES_PATH = config.devenv.root + "/sites";

    PYTHONPATH = benchInfra.appsPath config.devenv.root;
    REPO_ROOT = config.devenv.root;

    UV_PROJECT_ENVIRONMENT = config.env.DEVENV_STATE + "/uv-env";
    YARN_CACHE_FOLDER = config.env.DEVENV_STATE + "/yarn-cache";

    LD_LIBRARY_PATH = lib.makeLibraryPath (
        [
        pkgs.zlib
        pkgs.openssl
        pkgs.libffi
        pkgs.file.out
        pkgs.mariadb.client
        ]
        ++ cfg.extraLibraryPaths
    );
    }
    // (lib.optionalAttrs (cfg.siteName != "") {
    FRAPPE_SITE = cfg.siteName;
    })
    // cfg.extraEnv;