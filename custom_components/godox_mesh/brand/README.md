# Brand assets

Home Assistant renders these in the Integrations UI — the badge beside "Godox
Bluetooth Mesh" on the integration and device cards is `icon.png` from this
directory.

| file | size | used for |
|---|---|---|
| `icon.png` | 256×256 | the badge on integration and device cards |
| `icon@2x.png` | 512×512 | the same, on high-density displays |
| `logo.png` | 256×70 | wider contexts, such as the brand header |
| `logo@2x.png` | 512×140 | the same, on high-density displays |

## Source

The Godox wordmark, from Godox's own site (`godox.com/static/upload/uploadfiles/logo.svg`),
rendered to PNG. The square icons centre the wordmark on a transparent
background at 82 % width, since Godox publishes no separate square mark.

To regenerate at other sizes:

```bash
rsvg-convert -w 512 -h 140 -o logo@2x.png logo.svg
```

## Trademark

"Godox" and the Godox logo are trademarks of Godox Photo Equipment Co., Ltd.
This project is **not affiliated with, endorsed by, or sponsored by** them.

The mark is used here to identify the hardware this integration controls —
which is what an integration icon is *for*, and the same basis on which Home
Assistant carries logos for Hue, IKEA, Sonos, LIFX and hundreds of other
manufacturers whose integrations are likewise unofficial. Users scan the
integration list for the logo of the kit they own; showing something else would
be less honest, not more.

The full disclaimer is in the repository [README](../../../README.md#trademarks-and-affiliation).
