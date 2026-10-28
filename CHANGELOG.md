# Changelog

## 4.3.0
- **Much faster culling.** Moving through photos with the arrow keys now takes a few milliseconds instead of a quarter to half a second, and a pick or reject about 60 ms instead of 650 ms. The details panel updates in place and keeps its layout, and the grid only redraws what changed.
- **Faster loupe.** Stepping between photos is about 6x quicker (roughly 70 ms instead of 450 ms). The photo appears at once from its thumbnail while the sharp version loads, and holding an arrow key no longer queues a decode for every photo you pass.
- **Filmstrip in the loupe.** Neighbouring photos with their pick, reject and star marks. Click or scroll it to move. Press F to show or hide it.
- **Faster analysis.** About a third faster for JPEGs. Each folder is re-measured once in the background the first time you open it after updating. Your picks, rejects, stars and labels are kept, and thumbnails show straight away.
- **Faster Save as JPG and share-ready JPEGs.** Several photos are converted at once.
- **Smoother scrolling** with precision touchpads, and only the thumbnails on screen load first after a big scroll.
- **Counts next to each filter** (All, Picks, Rejects…). The counts in the top bar are clickable and jump to that filter. Clearer message when filters hide every photo.
- **Fixes:**
  - Save as JPG and 100% zoom on RAW-only photos used the camera's small embedded preview on some cameras (older Sony, Olympus), not the full-resolution image.
  - "Remove from group" was not saved, so the photo returned to its group after reopening.
  - Organise could apply an old preview after you changed the template, mode or scope. It now asks you to preview again.
  - Writing XMP sidecars no longer freezes the window on big folders, and it can be stopped.
  - The decisions file is always written safely (temp file + rename) after moving files.

## 4.2.0
- **Save selected as JPG.** Select photos in Cull or Groups, then press Ctrl+J, right-click > "Save N as JPG…", or use the button on the Export screen. RAW, HEIC and PNG files become full-size, high-quality (95) JPEGs with their EXIF kept. Originals are never touched, and it appears in History so it can be undone.
- **Updates keep your place.** Settings, recent folders, analysis caches and decisions live outside the program, so installing 4.2 over an existing copy changes nothing of yours. Added `Update-PhotographyTools.bat` (in the update zip) for portable copies.

## 4.1.0
- **Import from memory card.** Cards are detected automatically. Photos are sorted into dated folders, RAW+JPEG pairs and sidecars stay together, and photos already imported are skipped. Free space is checked first and every copy is verified.
- **Insights screen.** Keeper rate, quality and sharpness spread, a shooting timeline, and breakdowns by camera, lens, focal length, aperture, ISO and shutter speed.
- **Share-ready JPEGs.** Resize (Instagram, web, Full HD, 4K or full size), sharpen for screens and add an optional watermark, with a live preview.
- **Find similar photos** (right-click any photo).
- **Delete originals after exporting.** Only photos with a verified copy are removed, and they go to the Recycle Bin.
- **Welcome screen** with your recent folders. Settings and About windows.
- **Animations:** sliding nav indicator, pop-up stamps when you pick, reject or rate, a confirmation flash in the loupe, a cross-fade between photos, toasts that slide in (with Undo), a smoothed progress bar, numbers that count up and charts that grow in. Animations can be turned off in Settings.
- **Packaging:** app icon, one-click `build.bat`, Windows version info, an Inno Setup installer (Start menu, desktop shortcut, folder right-click), and a GitHub Actions build.
- Shortcuts: Ctrl+F search, Ctrl+I import, F11 full screen, Ctrl+, settings.

## 4.0.0
- Rebuilt as one app: Cull, Groups, Auto-Cull, Organise, Export and History in a single window, sharing one analysis pass.
- New sharpness measure based on edge steepness, independent of subject matter, with a focus map and motion-blur detection.
- Better grouping (exposure-robust, chain-resistant), duplicates found across the whole shoot.
- XMP sidecars are Lightroom-compatible and merged, never overwritten. No permanent deletes, and every file operation can be undone.
