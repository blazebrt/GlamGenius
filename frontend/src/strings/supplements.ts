/**
 * Every word the supplement detail says, in one place (Step 13).
 *
 * The supplement surface reports what a label says as it was recorded, who
 * recorded it, and what is missing. It never tells anyone what to take, how
 * much, whether to start or stop, whether a supplement suits them, or what it
 * will do. None of these words may appear here in the app's own voice: take
 * (as an instruction), dose, dosage, daily intake, total, too much, exceed,
 * safe, unsafe, toxic, spoiled, ineffective, deficient, recommend, should,
 * better, best, verified, official, scanned.
 *
 * Provenance is stated plainly so that something a person typed never reads
 * as scanned, manufacturer-supplied, regulator-issued or machine-checked.
 *
 * Checked against the six rules in LEGAL_RULES.md, the same as every other
 * string file.
 */
export type SupplementProvenance =
  | 'you_entered'
  | 'you_entered_not_confirmed'
  | 'read_from_photo_confirmed_by_you'
  | 'read_from_photo_not_confirmed'
  | 'unknown_source';

export type SupplementExpiryState = 'past' | 'coming_up' | 'current' | 'unknown';

export type ChemistryWithheldReason =
  | 'hydration_not_stated'
  | 'salt_form_not_stated'
  | 'composition_varies'
  | 'exact_formula_not_established'
  | 'form_not_stated';

export const S = {
  heading: 'What the label lists',
  intro: 'As you recorded it. Amounts are shown per product, exactly as printed.',
  empty: 'No label details added yet.',
  notEnoughInformation: 'Not enough information',

  provenance: {
    you_entered: 'You entered this',
    you_entered_not_confirmed: 'You entered this · not confirmed yet',
    read_from_photo_confirmed_by_you: 'Read from your photo · confirmed by you',
    read_from_photo_not_confirmed: 'Read from your photo · not confirmed yet',
    unknown_source: 'Source not recorded',
  } satisfies Record<SupplementProvenance, string>,
  awaitingConfirmation: 'Confirm this detail before it is compared with your other products.',
  confirm: 'Confirm this detail',

  printedAmount: 'Printed amount',
  amountNotAdded: 'Amount not added',
  printedServingText: 'Printed serving text',
  servingNotAdded: 'Serving text not added',

  form: {
    label: 'Form',
    notStated: 'The name you recorded does not say which form.',
    notEnoughInformation: 'Not enough information to identify the exact form.',
  },

  chemistry: {
    label: 'Package chemistry',
    /** `{element}` and `{percent}` are filled from the calculation. */
    calculated: '{percent}% of this compound’s weight is {element}.',
    basis: 'Calculated from the compound name and standard atomic weights. This is chemistry, not how much your body takes in.',
    withheld: {
      hydration_not_stated: 'Not calculated: the name does not say which hydrated form this is.',
      salt_form_not_stated: 'Not calculated: the name does not say which salt of this compound it is.',
      composition_varies: 'Not calculated: this form’s make-up differs between makers.',
      exact_formula_not_established: 'Not calculated: the exact formula is not established.',
      form_not_stated: 'Not calculated: the form is not stated.',
    } satisfies Record<ChemistryWithheldReason, string>,
  },

  knowledge: {
    label: 'Published research',
    none: 'Not enough information from a reviewed, published source.',
    sourcesDiffer: 'Sources differ',
    openSource: 'Open source',
    evidence: 'Evidence grade',
  },

  missingLabel: 'Still missing',
  missing: {
    amount: 'printed amount',
    unit: 'unit',
    serving_text: 'serving text',
    expiry_date: 'expiry date',
    label_components: 'label details',
    confirmation: 'your confirmation',
  } as Record<string, string>,

  expiry: {
    past: 'Past the date you recorded.',
    coming_up: 'Date coming up.',
    current: 'Date recorded: {date}',
    unknown: 'Expiry date not added.',
  } satisfies Record<SupplementExpiryState, string>,

  overlap: {
    heading: 'Also listed on your other products',
    /** `{count}` is the number of products, this one included. */
    line: 'Listed on {count} products you recorded.',
    here: 'On this label: {names}',
    other: '{product}: {names}',
    note: 'This compares names only. Amounts are never added together.',
  },

  weDoNot: {
    heading: 'What we do not do',
    lines: [
      'Say how much to take or how often',
      'Say whether to start or stop anything',
      'Say what a supplement will do for you',
      'Answer questions about medicines, pregnancy, children or health conditions',
    ],
  },

  editor: {
    heading: 'Label details',
    boundary: 'Package facts only. We do not tell you how much to take or whether a supplement is suitable for you.',
    open: 'Add or edit label details',
    close: 'Done editing label details',
  },

  photo: {
    action: 'Read label from photo',
    camera: 'Take a photo of the label',
    library: 'Choose a photo of the label',
    cancel: 'Not now',
    explain: 'We copy what is printed. Each detail stays unconfirmed until you check it.',
    busy: 'Reading the label…',
    done: 'Read from your photo. Confirm each detail before it is compared with your other products.',
    empty: 'We could not read any label details from that photo. You can add them yourself.',
    failed: 'We could not read that photo. Nothing was added.',
    retry: 'Try that photo again',
    cameraPermission: 'Camera permission is needed to photograph the label.',
  },

  a11y: {
    component: (name: string) => `Label detail: ${name}`,
    confirm: (name: string) => `Confirm ${name}`,
    openSource: (source: string) => `Open the published source: ${source}`,
  },
};

/** Who put a label fact here — the same mapping the server uses. */
export const factProvenance = (source: string, verificationState: string): SupplementProvenance => {
  const confirmed = verificationState === 'confirmed';
  if (source === 'user_declared') return confirmed ? 'you_entered' : 'you_entered_not_confirmed';
  if (source === 'photo_extracted') return confirmed ? 'read_from_photo_confirmed_by_you' : 'read_from_photo_not_confirmed';
  return 'unknown_source';
};

export const fill = (template: string, values: Record<string, string | number>): string =>
  template.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? String(values[key]) : match));
