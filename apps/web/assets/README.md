# assets

Empty, and worth a note rather than a deleted directory.

**This held `chess-pieces.ttf`** — a 2.9 KB subset of DejaVu Sans containing only `U+265A`–`U+265F`
(♚♛♜♝♞♟), vendored because a social card is rendered on the server by Satori, which has no access to
system fonts, and a production host without DejaVu would have drawn every piece as a tofu box.

It is gone because the cards now draw `react-chessboard`'s `defaultPieces` — the same Cburnett SVGs
the site itself renders, which `PlayerBar` already used for captured pieces. A card and the page it
links to show the same shapes, and the pieces carry their own stroke, so a black piece reads on a
dark square in a way a single-weight glyph never did.

If a card ever needs a *text* face of its own, this is where it goes, and the reason above is why:
`ImageResponse` ships a default face for text and nothing else.
