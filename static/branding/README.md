# Institutional logo assets

This directory holds optional per-institution logo files for the EduTrack
sidebar identity area.

## Naming convention

    static/branding/logo-<INSTITUTION-CODE>.svg

Examples: `logo-AUN.svg`, `logo-UNILAG.svg`

The institution code must match the `Institution.code` value exactly (for
example `AUN`). The code is case-sensitive on case-sensitive filesystems.

## How it is resolved

`accounts.context_processors.institution_branding` resolves the logo in this
order:

1. the institution's own `logo_url` field (set by a platform administrator on
   the "Branding" page in institution management);
2. a file in this directory following the naming convention above;
3. otherwise the interface shows an honest monogram placeholder
   (the institution code) until a real asset is supplied.

No official university logo is bundled with EduTrack, and none is fabricated.
The American University of Nigeria's own communications office should supply
`logo-AUN.svg`; it will appear automatically once the file is present.

## Asset guidance

- Square (1:1) SVG with the logo on a transparent or white background; the
  sidebar letterboxes it into a 40x40 box with `object-fit: contain`, so any
  square source scales without distortion.
- Keep the file small (a few KB). PNG may also be referenced through the
  institution's `logo_url` field if a raster export is all that exists.
