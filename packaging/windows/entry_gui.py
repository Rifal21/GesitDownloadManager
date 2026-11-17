"""PyInstaller entry point for gesit.exe (windowed)."""
import sys

from gesit.gui.app import main

sys.exit(main())
