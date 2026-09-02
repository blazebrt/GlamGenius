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

/** Interpolation: `t(S.why.nutrients.highSaturatedFat, { source: 'palm oil' })`. */
export const t = (template: string, values: Record<string, string | number> = {}): string =>
  template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? `{${key}}`));

export const S = {
  // -----------------------------------------------------------------------
  // The letter itself. Three words at most: the colour has already answered.
  // -----------------------------------------------------------------------
  grade: {
    A: { verdict: 'BUY', band: 'green' },
    B: { verdict: 'BUY', band: 'green' },
    C: { verdict: 'WAIT', band: 'yellow' },
    D: { verdict: 'SKIP', band: 'red' },
    E: { verdict: 'SKIP', band: 'red' },
  },

  // -----------------------------------------------------------------------
  // Primary screen. Three lines of text. Nothing technical.
  // -----------------------------------------------------------------------
  primary: {
    /** Line 1. Under ten words, and it says what to DO. */
    actionA: 'The label has fewer decision flags.',
    actionB: 'The label has some processing flags.',
    actionC: 'The label has product facts to consider.',
    actionD: 'The label has multiple product flags.',
    actionE: 'The label has a strong product concern.',
    actionNotGraded: 'Cooking ingredient. No letter applies.',
    actionUnknown: 'We could not read this label.',
    decisionBuy: 'BUY',
    decisionWait: 'WAIT',
    decisionSkip: 'SKIP',
    reasonSugar: 'Sugar is the main negative.',
    reasonSalt: 'Salt is the main negative.',
    reasonProcessing: 'Processing is the main negative.',
    reasonRefinedGrain: 'Refined grain is the main negative.',
    reasonSaturatedFat: 'Saturated fat is the main negative.',
    reasonTotalFat: 'Total fat is the main negative.',
    reasonAddedSugarShare: 'Added sugar is the main negative.',
    reasonTransFat: 'Trans fat is the main negative.',
    reasonAdditive: 'An additive is the main negative.',
    reasonNaming: 'The name needs more detail.',
    reasonLabelFacts: 'The label has product facts to consider.',

    /** Line 2. One declared number, with the basis it was measured on. */
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
  officialRecords: {
    title: 'Official FSSAI record',
    recallFound: 'This exact pack appears in an official FSSAI food recall record.',
    recallId: 'Recall ID',
    status: 'Recall status',
    statusUnavailable: 'Not stated',
    startDate: 'Recall start date',
    terminationDate: 'Recall termination date',
    reason: 'Reason recorded by FSSAI',
    nature: 'Nature of recall',
    checked: 'Official records last checked',
    // Observation, never conclusion. A record missing from the latest export
    // has not been withdrawn, cleared or resolved — it simply was not in that
    // download, and saying more than that would be inventing a fact.
    observedInLatest: 'Record observed in latest checked FSSAI export',
    lastObserved: 'Record last observed in FSSAI export',
    openSource: 'Open official FSSAI record',
  },
  communityObservations: {
    // "Observations" and "reported", never "problems", "issues" or "warnings".
    // A shopper saw something; that is the whole claim being made.
    heading: 'Shopper observations',
    // Shown with every block, never behind a tap: what this is, and what it
    // is not. The two things it is not are the two it could be mistaken for.
    disclosure: 'Reported by shoppers. Not laboratory testing or an official finding.',
    checked: 'Reported in the last 90 days',
    batch: 'Batch',
    reportAction: 'Report what you saw',
    signInToReport: 'Sign in on this phone to send a report.',
    photoRequired: 'Add a photo of what you saw.',
    photoAction: 'Add photo',
    batchCaptureRequired: 'Capture the pack label first so the batch can be matched.',
    captureLabelAction: 'Capture pack label',
    // "Saved", never "verified" or "confirmed": we know a shopper sent it, and
    // nothing more than that.
    reportSaved: 'Observation saved.',
    withdrawn: 'Observation withdrawn.',
    // Backend prose never becomes customer copy; unrecognised reasons land here.
    submitFailed: 'That did not send. Please try again.',
    submit: 'Send observation',
    cancel: 'Cancel',
    withdraw: 'Withdraw my observation',
    brandRightOfReply: 'Brand right of reply',
    chooseObservation: 'What did you see?',
    // One line per code. Each states what was seen, and stops there.
    observation: {
      barcode_result_differs_from_pack: 'the barcode result looked different from the pack',
      ingredients_list_differs_from_app: 'the ingredient list looked different',
      nutrition_panel_differs_from_app: 'the nutrition panel looked different',
      pack_size_differs_from_app: 'the pack size looked different',
      date_marking_unreadable: 'the date marking could not be read',
      seal_broken: 'a broken seal',
      pack_leaking: 'a leaking pack',
      pack_swollen: 'a swollen pack',
      // States what was seen. Whether the material belongs there, and whether
      // that makes anything unsafe, are conclusions the app does not draw.
      visible_foreign_material: 'visible material inside the pack',
      insect_observed: 'an insect',
    } as Record<string, string>,
    // "3 shoppers reported a broken seal". A count and an observation.
    reportedBy: (count: number, observation: string) =>
      `${count} ${count === 1 ? 'shopper' : 'shoppers'} reported ${observation}`,
  },
  labelReview: {
    basis: 'Basis',
    basisPer100g: 'Per 100 g',
    basisPer100ml: 'Per 100 ml',
    basisMissing: 'Basis could not be read',
    missingConfirmationReference: 'We could not keep a confirmation reference for that read. Try again.',
    saveFailed: 'We could not save that just now. Check your connection and try again.',
  },
  taxonomy: {
    packaged_food: 'Packaged food', whole_minimally_processed: 'Whole or minimally processed food',
    culinary_ingredient: 'Cooking ingredient', biscuit: 'Biscuit', cereal: 'Cereal',
    beverage: 'Beverage', dal: 'Dal', ghee: 'Ghee', cooking_oil: 'Cooking oil', salt: 'Salt',
    other_packaged_food: 'Packaged food',
  },
  provenance: {
    confirmed: 'Confirmed from pack',
    catalogue: 'Catalogue data',
    unknown: 'Product data source unavailable',
  },
  factors: {
    negatives: 'Negatives', positives: 'Positives', noNegatives: 'No product flags were found in the available label facts.',
    noPositives: 'No positive label facts were available.', source: 'Source', details: 'What this means',
    declared: 'Declared on label', no_concern_found: 'No concern found', worth_knowing: 'Worth knowing',
    worth_caution: 'Worth caution', flagged: 'Flagged', not_permitted: 'Not permitted',
    not_enough_information: 'Not enough information', lower_processing_group: 'Less processing on this label.',
    declared_on_label: 'This amount is declared on the label.',
    per_100_g: 'per 100 g', per_100_ml: 'per 100 ml', pack: 'per pack',
    lower_processing: 'This product has more processing flags.', lower_sugar: 'The label shows a sugar flag.',
    lower_salt: 'The label shows a salt flag.', lower_fat: 'The label shows a fat flag.',
    lower_additive: 'An ingredient on the label is flagged.', lower_naming: 'The name and declared ingredient details do not fully match.',
    lower_label_fact: 'A label fact lowered the grade.',

    /**
     * The name of the thing each row is about.
     *
     * Without these the row shows a status and a number with nothing to attach
     * them to — "High. 26.4 g per 100 g" of what?
     */
    label_sugar: 'Sugar', label_salt: 'Salt', label_sodium: 'Sodium',
    label_saturated_fat: 'Saturated fat', label_total_fat: 'Total fat',
    label_processing: 'Processing', label_refined_grain: 'Refined grain',
    label_trans_fat: 'Trans fat', label_added_sugar_share: 'Added sugar',
    label_named_ingredient: 'Named ingredient',
    label_protein: 'Protein', label_fibre: 'Fibre',

    /** What each finding means, in the words the row prints. */
    high: 'High',
    moderate: 'Moderate',
    high_sugar: 'High in sugar',
    high_salt: 'High in salt',
    high_sodium: 'High in sodium',
    high_saturated_fat: 'High in saturated fat',
    high_total_fat: 'High in total fat',
    highly_processed: 'Highly processed',
    processed: 'Processed',
    refined_grain_main_ingredient: 'The main ingredient is a refined grain.',
    partially_hydrogenated_oil: 'The label lists partially hydrogenated oil.',
    added_sugar_dominates_energy: 'Added sugar supplies most of the energy.',
    additive_black: 'Not permitted in food sold here.',
    additive_red: 'Usage is regulated; this rule flags it.',
    additive_amber: 'Worth knowing about.',
    named_ingredient_share: 'Less of the named ingredient than the name suggests.',
    named_ingredient_not_declared: 'The pack does not declare how much of it there is.',
    of_product: 'of the product',
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
  // While it loads, and when it does not.
  // -----------------------------------------------------------------------
  loading: {
    working: 'Reading this pack',
    failedTitle: 'We could not load this',
    // States what happened. It does not blame the phone or promise a cause.
    failedBody: 'The verdict did not arrive. Your connection may be down.',
    retry: 'Try again',
    back: 'Go back',
  },

  // -----------------------------------------------------------------------
  // Why? — the four things that decided it.
  // -----------------------------------------------------------------------
  why: {
    title: 'Why this letter',
    sourceLink: 'Source',
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
    noConcernFound: 'No concern found',
    worthKnowing: 'Worth knowing',
    worthCaution: 'Worth caution',
    flagged: 'Flagged',
    notPermitted: 'Not permitted',
    notEnoughInformation: 'Not enough information',
    empty: 'The pack did not carry an ingredient list.',

    /**
     * The deeper explanation behind the `?` on an ingredient row.
     *
     * This is the fuller answer, never the only one: the row itself already
     * says what the ingredient is and what it does. Somebody who never taps
     * `?` has still been told the useful thing.
     */
    explainAction: 'What this means',
    explainTitle: 'About this ingredient',
    whatItDoes: 'What it does',
    whyFlagged: 'Why it is flagged',
    exactRule: 'The exact rule we applied',
    authorityPosition: 'What the authority says',
    ourInterpretation: 'How we read it',
    evidenceStatus: 'How settled this is',
    openSource: 'Open the source',
    noRule: 'No rule of ours flags this one.',
    noInterpretation: 'We add nothing to what the authority says here.',
    noNote: 'The pack states it; we carry no further note on it.',
    noAuthority: 'No authority position is recorded for this one yet.',
    unreviewedRule: 'This rule has not completed review yet.',
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
    openSource: 'Open the source for {name}',
    explain: 'Explain {name}',
  },
} as const;

export type StringTable = typeof S;
