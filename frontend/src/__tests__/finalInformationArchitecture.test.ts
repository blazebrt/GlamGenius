import {
  CARE_CATEGORIES,
  LEGACY_HIDDEN_TAB_ROUTES,
  PRIMARY_TAB_LABELS,
  PRIMARY_TABS,
  STYLE_CATEGORIES,
  categoriesForDomain,
} from '../navigation/finalIA';

describe('VC-08 final information architecture', () => {
  it('has exactly five visible peer tabs with customer-facing labels', () => {
    expect(PRIMARY_TABS).toEqual(['today', 'style', 'care', 'plan', 'you']);
    expect(PRIMARY_TAB_LABELS).toEqual(['Today', 'Style', 'Care', 'Plan', 'You']);
    expect(PRIMARY_TABS).toHaveLength(5);
    expect(PRIMARY_TAB_LABELS.join(' ')).not.toMatch(/Inventory|Style Me|Planner|Services|Beauty/);
  });

  it('keeps legacy destinations routable but outside primary navigation', () => {
    expect(LEGACY_HIDDEN_TAB_ROUTES).toEqual(expect.arrayContaining([
      'home', 'inventory', 'style-me-tab', 'planner', 'profile', 'services', 'scan-tab', 'history',
    ]));
    expect(PRIMARY_TABS).not.toEqual(expect.arrayContaining(LEGACY_HIDDEN_TAB_ROUTES));
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
