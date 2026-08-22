# assets

## `chess-pieces.ttf`

A subset of **DejaVu Sans** containing only the six filled chess glyphs, `U+265A`–`U+265F`
(♚♛♜♝♞♟). 2.9 KB — the full face is ~750 KB, and the OG card needs nothing else from it.

It is vendored rather than loaded from the system because the OG image is generated on the server
at request time, and a production host with no DejaVu installed would render every piece as a
tofu box. A font the card depends on has to travel with the code.

**Only the filled glyphs are subset, on purpose.** The outline glyphs `U+2654`–`U+2659` are the
"white" pieces, and a white outline on a light square is close to invisible. Both colours are
drawn with the filled shapes instead and separated by CSS `color`, which is legible on every
square and matches how the pieces read on a physical board.

Licence: Bitstream Vera Fonts Copyright, reproduced verbatim in `chess-pieces.LICENSE.txt`.
The subset keeps the DejaVu name, which the licence permits — it forbids only reusing the
"Bitstream" or "Vera" names.
