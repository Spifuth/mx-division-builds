import { describe, it, expect, vi } from "vitest";

// dataImporter fetches 20 CSVs and reads localStorage at import time.
// statsService imports it, so without this mock the suite cannot even load.
vi.mock("./dataImporter", () => ({
  gearData: {},
  skillsData: {},
  weaponsData: {},
  specializationList: {},
  IsEverythingLoadedPromise: Promise.resolve(),
  VendorData: Promise.resolve({ Gear: {}, Weapons: [] }),
}));

import statsService from "./statsService";

describe("flatWeaponDamage", () => {
  it("applies additive damage percentages to base damage", () => {
    // 1000 base, +10% AWD, +5% weapon-type, +0% generic => 1150
    expect(statsService.flatWeaponDamage(1000, 10, 5, 0)).toBe(1150);
  });

  it("returns a number, not a string", () => {
    // .toFixed(0) returns a string. Every caller multiplies the result, so JS
    // coerces and the bug stays invisible -- until something uses + and gets
    // "1150100" instead of 1250.
    expect(typeof statsService.flatWeaponDamage(1000, 0, 0, 0)).toBe("number");
  });
});
