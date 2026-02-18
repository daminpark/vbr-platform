# Storage Locations Reference

## Overview
Two properties: 193 and 195 Vauxhall Bridge Road. Locations coded by house number + letter.
Shared layouts where noted (193/5 = same layout in both houses).

---

## Outside / External Storage

### 193.W — Kitchen Yard (Basement Level, Outside)
Access: Go outside from kitchen. Some rain cover to cupboard, none to shed.
Two separate spaces:

**193.W Cupboard**
- Contains: Electricity meter. All paints, a can of PVC.
- Quirks: Very dusty inside. Under cover (don't get rained on reaching it).
- Security: Unlocked, but guests rarely come here.
- Notes: Not ideal for anything that needs to stay clean. Good for paints/chemicals that don't mind dust.

**193.W Shed (Toolshed)**
- Contains: Stacked shelves on both sides, middle currently empty.
- Quirks: Cramped, dark, fully exposed to rain getting to it. London winter = wet trip every time.
- Security: Locked.
- Notes: Main tool storage. Dark and inconvenient in winter. Consider bringing bags inside during renovation work rather than making repeated trips.

### 195.W — Outside Storage Area
**195.W Keter Box 1 (Sheets)**
- Contains: All spare sheets/linen.
- Notes: Outdoor box — check for damp periodically.

**195.W Keter Box 2 (Door Hardware)**
- Contains: Door hardware — currently a jumble, needs sorting. Unknown exact contents.
- TODO: Audit contents, organise, catalogue what's actually in here.

**195.W Cupboard (Large Appliances)**
- Contains: Dehumidifier, pressure washer.
- Notes: Good for bulky items that don't need frequent access.

### 193.P — Patio (Ground Floor, via Guest Room)
**193.P Keter Box**
- Contains: Some plumbing bits, some electrical parts.
- Quirks: Accessible only through a guest room. Not ideal for frequent access when room is occupied.
- Notes: Good for overflow trade supplies. Access is awkward if guests are in.

---

## Indoor — Basement Level

### 193/5.K — Kitchen (Basement)
Exists in both houses, same layout.
- Under sink: Dishwasher tablets and some cleaning bits.
- Cabinets: Some free. Iron stored in one cabinet.
- Notes: Best for kitchen-related supplies. Limited space. Guest-accessible.

### 193/5.0 — Dining Room (Basement, next to Kitchen)
Exists in both houses. **Guests have access.**
- Storage: Whole wall of shelves. Lots of capacity.
- Quirks: Guest-visible. Anything stored here needs to look tidy or be in closed containers.
- Notes: High-capacity storage but must be presentable. Good for: neatly boxed spare toiletries, guest supplies (extra towels, pillows, adapters), board games/guest books. Bad for: cleaning chemicals, random tools, anything messy.

### 193/5.Y — Utility / Laundry Area
Exists in both houses.
- Contains: Washing machine, dryer.
- Purpose: Linen and laundry related stuff.
- Notes: Keep this focused on laundry — detergent, fabric softener, spare linen, ironing supplies.

### 193/5.Z — Cleaning Storage
Exists in both houses.
- Lower area: Day-to-day cleaning products and tools.
- Upper shelves: Fairly difficult for cleaners to reach regularly, but a step stool is permanently hanging in this room. Fair amount of space.
- Notes: Primary cleaning supply location. Upper shelves ideal for backup/bulk cleaning stock — spare bottles, bulk packs of sponges/cloths/bin bags, seasonal items. Cleaners pull replacements from upper shelves when lower shelf runs low. Step stool makes this workable.

### 193.V — Basement, Opposite Kitchen (Luggage Storage Area)
- Primary use: Guest luggage storage.
- Around the corner (less accessible part): Currently has carpet and underlay.
- Notes: The less accessible corner is fine for renovation materials. Don't block guest luggage area.

### 195.V — Basement, Opposite Kitchen (Luggage Storage Area)
- Primary use: Guest luggage storage.
- Less accessible part: Currently has tiles, tiling materials (grout, cement), many bags of filler.
- Notes: Renovation material storage. Same as 193.V — keep guest luggage area clear.

---

## Indoor — Upper Floors

### 195.U — First Floor Wardrobe (Locked)
- Contains: Personal stuff, two guest cots (for first and second floor rooms).
- Security: Locked.
- Notes: Limited access. Keep personal items and guest equipment (cots) here.

### 193.U — Wardrobe (Unlocked?)
- Contains: Some stuff for sale, two cots, spare TV. Largely empty.
- Notes: Good overflow capacity. Currently underutilised. Could absorb items that don't have a clear home elsewhere.

---

## Location Selection Logic (for AI suggestions)

When deciding where a new item should go, consider:
1. **Who needs it?** Cleaners daily → 193/5.Z or 193/5.Y. Renovation/trade → 193.W Shed or .V areas.
2. **Guest-visible?** If yes, must be tidy → avoid 193/5.0 dining shelves for messy items, or use closed containers.
3. **Frequency of access?** Daily → indoor, easy reach. Seasonal/rare → shed, upper shelves, .V areas, 193.U.
4. **Weather exposure?** Outdoor locations (193.W shed, Keter boxes) — nothing that can't handle damp/cold.
5. **Size?** Bulky items → 195.W cupboard, 193.U, .V areas. Small items → 193/5.Z upper shelves, kitchen cabinets.
6. **Category grouping?** Keep like with like — all plumbing together, all electrical together, all cleaning together.
7. **Security?** Valuable tools → 193.W shed (locked) or 195.U (locked). Don't leave expensive items in guest-accessible areas.
