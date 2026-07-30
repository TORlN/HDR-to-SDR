# Trademark and Branding Notice

The source code of HDR to SDR Converter is released under the MIT License (see
`LICENSE`). That grant covers **code only**.

The following are **not** covered by the MIT License and remain the exclusive
property of Torin Nelson:

- The names **"HDR to SDR Converter"** and **"HDR to SDR"** as applied to this
  software, and the domain **hdrtosdr.com**.
- The application icon and logo artwork, including `logo/icon.ico`,
  `logo/icon.png`, and all derivatives (see `logo/NOTICE.md`).

## If you fork this project

You are free to fork, modify, and redistribute the code under the MIT License.
When you do, please:

1. **Rename your fork.** Do not distribute a build that presents itself as
   "HDR to SDR Converter". Users cannot tell whose software they are running
   when two different builds carry the same name.
2. **Use your own icon.** Replace `logo/icon.ico` and `logo/icon.png` with your
   own artwork.
3. **Change the installer's `AppId`.** The `AppId` GUID in `installer.iss` is
   this product's Windows identity. Shipping an installer that reuses it makes
   your build collide with this one in Add/Remove Programs and in upgrade
   detection. Generate a fresh GUID in Inno Setup via Tools → Generate GUID.
4. **Set your own `AppPublisher` and `AppURL`.**

These are requests grounded in avoiding user confusion, not additional
conditions on the MIT license of the code.
