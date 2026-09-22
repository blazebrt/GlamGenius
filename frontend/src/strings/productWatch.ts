/**
 * Every word Product Watch says, in one place.
 *
 * Product Watch is a quiet, secondary control: it records that a person asked
 * to hear about one exact pack, and nothing more. So the copy states what the
 * control does and never characterises the product. None of these words may
 * appear here: danger, dangerous, unsafe, safe, cleared, warning, alert,
 * urgent, improved, worse, better, reformulated, fixed, healthier, new recall.
 * A later official record is a current fact about the register, not news
 * about the product, and nothing here may suggest otherwise.
 *
 * Checked against the six rules in LEGAL_RULES.md, the same as every other
 * string file.
 */
export const S = {
  heading: 'VERIFIED CHANGES',
  /** The one action before a watch exists. */
  watch: 'Watch verified changes',
  /** The state once it does. Shown whether or not push is on. */
  watching: 'Watching verified changes',
  /** What watching means, read before the tap. */
  explain:
    'You can be told if an official FSSAI record comes to match this exact pack, or if a sourced change becomes available. Nothing is sent when you start.',
  /** The watch belongs to a pack of this product scanned earlier. */
  otherPack: 'You are watching a pack of this product that you scanned earlier.',
  watchThisPack: 'Watch this pack instead',
  stop: 'Stop watching',
  /** Not a refusal of the person — a statement of what is missing. */
  needsConfirmedPack: "Confirm this pack's label on this phone to watch it.",
  /** Watching and being told are separate choices. */
  deliveryOff:
    'Notifications for watched products are off. You can change that in Notifications settings.',
  failed: 'Could not update. Nothing changed.',
  a11y: {
    watch: 'Watch verified changes for this product',
    watching: 'Watching verified changes for this product',
    watchThisPack: 'Watch this pack of the product instead',
    stop: 'Stop watching verified changes for this product',
  },
  /** The topic's row on the Notifications screen. */
  notificationsRow: 'Product watch',
};
