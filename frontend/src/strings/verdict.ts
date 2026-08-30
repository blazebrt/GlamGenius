/**
 * Every word the verdict screens say, in one place.
 *
 * Nothing user-facing is written inline anywhere in these screens. That is not
 * tidiness — it is how the strings stay reviewable against LEGAL_RULES.md
 * without reading React, and how they get translated without touching a
 * component. A reviewer reads this file.
 *
 * The six writing rules these were checked against:
 *   1. State, do not characterise.       "22 g of sugar", not "loaded with sugar"
 *   2. Cite, do not assert.              every claim names where it came from
 *   3. Compare products, not people.     never "you should", never "your diet"
 *   4. Never mock a brand.               no product is called junk, fake or a scam
 *   5. State missing data, do not fill.  "we could not read it" is an answer
 *   6. Show the source with a negative.  every red thing names its rule
 */

/** Interpolation: `t(S.primary.sugarSpoons, { spoons: 6 })`. */
export const t = (template: string, values: Record<string, string | number> = {}): string =>
  template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? `{${key}}`));

export const S = {
  // -----------------------------------------------------------------------
  // The letter itself. Three words at most: the colour has already answered.
  // -----------------------------------------------------------------------
  grade: {
    A: { verdict: 'Take it', band: 'green' },
    B: { verdict: 'Take it', band: 'green' },
    C: { verdict: 'Think', band: 'yellow' },
    D: { verdict: 'Leave it', band: 'red' },
    E: { verdict: 'Leave it', band: 'red' },
  },

  // -----------------------------------------------------------------------
  // Primary screen. Three lines of text. Nothing technical.
  // -----------------------------------------------------------------------
  primary: {
    /** Line 1. Under ten words, and it says what to DO. */
    actionA: 'A good everyday food. Keep it.',
    actionB: 'Simple and fine. Keep it.',
    actionC: 'Fine once in a while, not daily.',
    actionD: 'Better to put this back.',
    actionE: 'Put this back.',
    actionNotGraded: 'Cooking item. Use a small amount.',
    actionUnknown: 'We could not read this label.',

    /** Line 2. One number, in something you can picture. */
    sugarSpoons: '{spoons} spoons of sugar in one packet',
    sugarSpoonsOne: '1 spoon of sugar in one packet',
    saltPinches: '{pinches} pinches of salt in one packet',
    saltPinchesOne: '1 pinch of salt in one packet',
    oilSpoons: '{spoons} spoons of oil in one packet',
    oilSpoonsOne: '1 spoon of oil in one packet',
    proteinBowls: 'Protein of about {bowls} bowls of dal',
    noEverydayNumber: 'Nothing on this label stands out.',

    /** Line 3. A better one, with its price and its letter. */
    alternative: '{name}, ₹{price} — {grade}',
    alternativeLead: 'Better nearby',
    noAlternative: 'We have nothing better to suggest yet.',

    /** Buttons. */
    why: 'Why?',
    listen: 'Listen',
    listening: 'Reading it out',
    stopListening: 'Stop',
    share: 'Share',
    ingredients: 'What is in it',
    scanAnother: 'Scan another',
  },

  // -----------------------------------------------------------------------
  // Not graded, and not known. Both are answers, not failures.
  // -----------------------------------------------------------------------
  notGraded: {
    title: 'No letter for this one',
    body: 'This is something you cook with, not a food on its own. A letter would '
      + 'only tell you it is oil, or salt, or sugar.',
    quantityLead: 'How much',
    purityLead: 'Worth checking on the pack',
  },
  unknown: {
    title: 'We could not grade this',
    body: 'The label did not have everything we need. We do not guess.',
    missingLead: 'What was missing',
    missingIngredients: 'the ingredient list',
    missingPanel: 'the nutrition panel',
    helpUs: 'Send us a photo of the pack',
  },

  // -----------------------------------------------------------------------
  // Why? — the four things that decided it.
  // -----------------------------------------------------------------------
  why: {
    title: 'Why this letter',
    subtitle: 'Four things decide it. Tap any one to see the rule behind it.',
    tapToExpand: 'Tap to see the rule',
    ruleLead: 'The rule',
    sourceLead: 'Where this comes from',

    processing: {
      label: 'How much was done to it',
      plain: 'How far this is from the food it started as.',
      nova1: 'Nothing was added. This is close to the raw food.',
      nova2: 'This is a cooking ingredient.',
      nova3: 'A simple food with salt, sugar or oil added.',
      nova4: 'Made in a factory from things you would not cook with at home.',
      /** The technical word, only ever with its explanation attached. */
      term: 'NOVA group',
      termPlain: 'a way of sorting food by how much was done to it, not by its nutrients',
    },
    nutrients: {
      label: 'Sugar, salt and fat',
      plain: 'What the nutrition panel says, per 100 grams.',
      highSugar: 'High in sugar.',
      highSalt: 'High in salt.',
      highSaturatedFat: 'High in the kind of fat that comes from {source}.',
      nothingHigh: 'Nothing here is high.',
      satFatNotCounted: 'The fat here comes from the food itself, so it is not counted against it.',
      term: 'saturated fat',
      termPlain: 'the fat that is solid at room temperature, like ghee or palm oil',
    },
    additives: {
      label: 'Added chemicals',
      plain: 'Things added to keep it, colour it or change how it tastes.',
      none: 'Nothing on this label needs flagging.',
      red: '{name} is one we flag.',
      black: '{name} should not be in food sold here.',
      childColour: 'A colour is used in something sold to children.',
      term: 'additive',
      termPlain: 'anything added that is not food — a colour, a preservative, a thickener',
    },
    naming: {
      label: 'Is it what the name says',
      plain: 'How much of the thing on the front is actually inside.',
      good: 'Mostly {ingredient}, as the name says.',
      note: 'Less than half of this is {ingredient}.',
      low: 'Only {percent}% is {ingredient}.',
      notDeclared: 'The pack does not say how much {ingredient} is in it.',
      notPromised: 'The name does not promise any particular ingredient.',
    },
  },

  // -----------------------------------------------------------------------
  // The full ingredient list. Free, always.
  // -----------------------------------------------------------------------
  ingredients: {
    title: 'What is in it',
    subtitle: 'Every ingredient on the pack, in the order it is printed.',
    orderNote: 'The first one is the most of it.',
    unknownIngredient: 'We do not have a plain description for this one yet.',
    tierGreen: 'Ordinary',
    tierAmber: 'Worth knowing about',
    tierRed: 'We flag this one',
    tierBlack: 'Should not be in food sold here',
    tierPlain: 'Food',
    empty: 'The pack did not carry an ingredient list.',
  },

  // -----------------------------------------------------------------------
  // Report an error. One tap from any number.
  // -----------------------------------------------------------------------
  report: {
    trigger: 'Report an error',
    triggerShort: 'Wrong?',
    title: 'What is wrong here?',
    subtitle: 'Tap the closest one. It goes straight to us.',
    optionWrongNumber: 'A number is wrong',
    optionWrongIngredient: 'An ingredient is wrong or missing',
    optionWrongProduct: 'This is a different product',
    optionWrongGrade: 'The letter looks wrong',
    optionPackChanged: 'The pack has changed',
    optionSomethingElse: 'Something else',
    addPhoto: 'Add a photo of the pack',
    retakePhoto: 'Take it again',
    photoAdded: 'Photo added',
    notePlaceholder: 'Anything you want to add (not required)',
    submit: 'Send',
    sending: 'Sending',
    sent: 'Sent. Thank you — we check every one.',
    failed: 'We could not send that. It is saved and will go when you are back online.',
    cancel: 'Not now',
  },

  // -----------------------------------------------------------------------
  // Spoken aloud. Written to be heard, not read.
  // -----------------------------------------------------------------------
  voice: {
    graded: '{verdict}. {action} {number}',
    withAlternative: '{verdict}. {action} {number} Better nearby: {alternative}, {price} rupees, grade {grade}.',
    notGraded: 'No letter for this one. {body}',
    unknown: 'We could not grade this. The label did not have everything we need.',
    /** Read as "grade B", not "grade bee". */
    gradeSpoken: 'Grade {letter}',
    unavailable: 'Reading aloud is not available on this device.',
  },

  // -----------------------------------------------------------------------
  // Accessibility labels. Never shown, always spoken by a screen reader.
  // -----------------------------------------------------------------------
  a11y: {
    gradeBadge: 'Grade {letter}. {verdict}.',
    colourGreen: 'Green',
    colourYellow: 'Yellow',
    colourRed: 'Red',
    why: 'Why this letter',
    listen: 'Read this out loud',
    stop: 'Stop reading',
    share: 'Share this',
    report: 'Report an error on {subject}',
    ingredientRow: '{name}. {tier}. {description}',
    expandComponent: 'Show the rule behind {label}',
    collapseComponent: 'Hide the rule behind {label}',
  },
} as const;

export type StringTable = typeof S;
