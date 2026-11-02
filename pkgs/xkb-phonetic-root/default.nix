# pkgs/xkb-phonetic-root/default.nix
#
# A private xkb configuration root in which the base layout "bg" is the
# phonetic (ЯВЕРТЪ) layout instead of the BDS one.
#
# Kodi's libinput code passes an xkb layout name but never a variant
# (LibInputKeyboard::SetKeymap sets names.layout and leaves names.variant
# null), so the phonetic variant of bg is otherwise unreachable. With
# XKB_CONFIG_ROOT pointing here, asking Kodi for "bg" loads symbols/bg, whose
# default section includes bg(phonetic).
#
# modules/storage/tv-box.nix sets XKB_CONFIG_ROOT on tv-kodi, and
# kodi-keyboard-layout switches input.libinputkeyboardlayout between us and bg
# as the on-screen keyboard switches between English QWERTY and Bulgarian
# ЯВЕРТЪ.
{
  runCommand,
  xkeyboard-config,
}:

runCommand "xkb-phonetic-root" { } ''
  xkb="${xkeyboard-config}/etc/X11/xkb"
  if [ ! -d "$xkb" ]; then
    xkb="${xkeyboard-config}/share/X11/xkb"
  fi

  mkdir -p $out/symbols

  # Everything except symbols/bg is the distribution's own data.
  for dir in rules types keycodes compat geometry semantics; do
    if [ -d "$xkb/$dir" ]; then
      ln -s "$xkb/$dir" "$out/$dir"
    fi
  done
  for file in "$xkb"/symbols/*; do
    name=$(basename "$file")
    [ "$name" = bg ] && continue
    ln -s "$file" "$out/symbols/$name"
  done

  # Keep the distribution's file for its sections; "bg" itself becomes the
  # phonetic one.
  cp "$xkb/symbols/bg" "$out/symbols/bg_full"
  cat > $out/symbols/bg <<'EOF'
  // The phonetic (ЯВЕРТЪ) layout as bg, so Kodi's layout-only keymap can
  // reach it. The distribution's file is kept as bg_full for its sections.
  default partial alphanumeric_keys
  xkb_symbols "phonetic" {
      include "bg_full(phonetic)"
  };
  EOF
''
