-- Compositor config for the live tests. Omarchy's Hyprland defaults are
-- loaded when they are installed or mounted in, so windows meet the same
-- window and group rules as on the desktop the script restores; plain
-- Hyprland otherwise. The two monitors are headless outputs the test creates
-- through hyprctl; their positions are fixed here, as a real monitors.lua does.
local omarchy = (os.getenv("OMARCHY_PATH") or "/usr/share/omarchy") .. "/default/hypr"
local bootstrap = io.open(omarchy .. "/bootstrap.lua")
if bootstrap then
  bootstrap:close()
  dofile(omarchy .. "/bootstrap.lua")
  require("default.hypr.omarchy")
end

hl.monitor({ output = "MON-A", mode = "preferred", position = "0x0", scale = 1 })
hl.monitor({ output = "MON-B", mode = "preferred", position = "1920x0", scale = 1.25 })
-- The window the nested compositor opens on its parent, and the output of the
-- vkms card in run.sh's VM; only the two headless monitors above should exist,
-- as on a real machine.
hl.monitor({ output = "WAYLAND-1", disabled = true })
hl.monitor({ output = "Virtual-1", disabled = true })

hl.config({
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
  misc = { disable_hyprland_logo = true, force_default_wallpaper = 0 },
  -- A fresh home starts at lastVersion 0.0.0, so a test's second boot would
  -- open a "Hyprland Updated" window. Omarchy's defaults turn it off; CI has none.
  ecosystem = { no_update_news = true },
})
