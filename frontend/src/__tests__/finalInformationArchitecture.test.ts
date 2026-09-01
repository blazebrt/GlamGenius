import {
  CARE_CATEGORIES,
  LEGACY_HIDDEN_TAB_ROUTES,
  PRIMARY_TAB_LABELS,
  PRIMARY_TABS,
  STYLE_CATEGORIES,
  categoriesForDomain,
} from '../navigation/finalIA';

describe('Step 1 product-shell information architecture', () => {
  it('keeps Scan dominant and removes the appearance OS from visible tabs', () => {
    expect(PRIMARY_TABS).toEqual(['scan', 'you']);
    expect(PRIMARY_TAB_LABELS).toEqual(['Scan', 'You']);
    expect(PRIMARY_TABS).toHaveLength(2);
    expect(PRIMARY_TAB_LABELS.join(' ')).not.toMatch(/Today|Style|Care|Plan|Inventory|Style Me|Planner|Services|Beauty/);
  });

  it('keeps legacy destinations routable but outside primary navigation', () => {
    expect(LEGACY_HIDDEN_TAB_ROUTES).toEqual(expect.arrayContaining([
      'home', 'inventory', 'planner', 'profile', 'services', 'scan-tab', 'history', 'today', 'style', 'care', 'plan',
    ]));
    expect(PRIMARY_TABS).not.toEqual(expect.arrayContaining(LEGACY_HIDDEN_TAB_ROUTES));
    // Retired with the standalone Style Me entry points; no longer routable.
    expect(LEGACY_HIDDEN_TAB_ROUTES).not.toContain('style-me-tab');
  });

  it('keeps the all-seven-category inventory authority while enforcing domain collections', () => {
    expect(STYLE_CATEGORIES).toEqual(['wardrobe', 'shoes', 'accessories']);
    expect(CARE_CATEGORIES).toEqual(['beauty', 'hair', 'perfumes', 'supplements']);
    expect(new Set([...STYLE_CATEGORIES, ...CARE_CATEGORIES]).size).toBe(7);
    expect(categoriesForDomain('style')).toEqual(STYLE_CATEGORIES);
    expect(categoriesForDomain('care')).toEqual(CARE_CATEGORIES);
    expect(categoriesForDomain('unknown')).toBeUndefined();
  });
});
