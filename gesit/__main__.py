import sys

if "--native-host" in sys.argv:  # stdlib-only path: must not import Qt (started once per browser message)
    from .native_host import main as _host_main

    _host_main()
    sys.exit(0)

from .gui.app import main  # noqa: E402

sys.exit(main())
