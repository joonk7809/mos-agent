# 0009. First MOS ROM device: Pixel 7a (lynx)

- Status: Accepted (phone purchase deferred: emulators first, see 0010)
- Date: 2026-11-17
- Deciders: joon (answered pager #6); agent had recommended the Pixel 8a
- Pager issue: #6

## Context
The ROM needs a target phone to buy (refurbished, unlocked). `rom/build.sh` takes any Lineage-supported Pixel codename, so only the purchase depends on this. All candidates are on LineageOS 24.0 weekly builds with TheMuppets blobs (checked 2026-11-17 against LineageOS/hudson `lineage-build-targets` and TheMuppets `muppets.xml`).

## Decision
The first MOS ROM phone is a Pixel 7a (`lynx`): `rom/build.sh lynx`. It must be an unlocked (non-carrier) unit.

## Alternatives considered
- **Pixel 8a (`akita`)** - recommended by the agent for Google firmware support to about 2031; joon chose the cheaper 7a.
- **Pixel 9a (`tegu`)** - longest support (about 2032), most expensive.

## Consequences
Google firmware for the 7a ends around May 2028, so plan a move to a newer device or base before then. Nothing else in `rom/` depends on the model. Hardware/OS alternatives were compared on 2026-11-17 (doc: "MOS hardware and OS options"); that comparison still recommended the 7a + LineageOS.
