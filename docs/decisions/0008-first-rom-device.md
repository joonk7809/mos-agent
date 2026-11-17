# 0008. First MOS ROM device: Pixel 8a (akita)

- Status: Proposed
- Date: 2026-11-17
- Deciders: joon (pending); agent recommendation
- Pager issue: #6

## Context
The ROM needs a target phone to buy (refurbished, unlocked). `rom/build.sh` takes any Lineage-supported Pixel codename, so only the purchase depends on this. All candidates are on LineageOS 24.0 weekly builds with TheMuppets blobs (checked 2026-11-17 against LineageOS/hudson `lineage-build-targets` and TheMuppets `muppets.xml`).

## Decision
Recommend the Pixel 8a (`akita`): Google firmware support until about 2031, so Lineage support lasts; Tensor G3 with 8 GB RAM; mid-priced refurbished. Must be an unlocked (non-carrier) unit.

## Alternatives considered
- **Pixel 7a (`lynx`)** - cheapest, but Google firmware ends around 2028, so the ROM ages out sooner.
- **Pixel 9a (`tegu`)** - longest support (about 2032), most expensive.

## Consequences
`rom/build.sh` defaults to `akita`. If joon picks another phone, pass its codename; nothing else changes.
