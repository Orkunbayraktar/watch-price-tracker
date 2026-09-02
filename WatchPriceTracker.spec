"""Maintained Windows onedir build for Watch Price Tracker."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


PROJECT_ROOT = Path(SPECPATH)

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
tzdata_datas = collect_data_files("tzdata")

datas = [
	(str(PROJECT_ROOT / "templates"), "templates"),
	(str(PROJECT_ROOT / "static"), "static"),
]
datas += playwright_datas
datas += tzdata_datas

hiddenimports = sorted(
	set(
		playwright_hiddenimports
		+ [
			"apscheduler.executors.pool",
			"apscheduler.jobstores.memory",
			"apscheduler.triggers.cron",
			"tzdata",
		]
	)
)

a = Analysis(
	["launcher.py"],
	pathex=[str(PROJECT_ROOT)],
	binaries=playwright_binaries,
	datas=datas,
	hiddenimports=hiddenimports,
	hookspath=[],
	hooksconfig={},
	runtime_hooks=[],
	excludes=["pytest", "tests"],
	noarchive=False,
	optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
	pyz,
	a.scripts,
	[],
	exclude_binaries=True,
	name="WatchPriceTracker",
	debug=False,
	bootloader_ignore_signals=False,
	strip=False,
	upx=False,
	console=False,
	disable_windowed_traceback=False,
	target_arch=None,
	codesign_identity=None,
	entitlements_file=None,
)

coll = COLLECT(
	exe,
	a.binaries,
	a.datas,
	strip=False,
	upx=False,
	upx_exclude=[],
	name="WatchPriceTracker",
)
