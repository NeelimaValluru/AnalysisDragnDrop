/**
 * Turning "the document should now read like this" into the smallest edit that
 * says so.
 *
 * The host re-serializes the whole document after every intent, but replacing
 * the whole file with the result would be needlessly destructive: it moves the
 * cursor and collapses the selection in any text editor open on the same file,
 * and it makes every undo step look identical in the timeline. Trimming the
 * common prefix and suffix costs two loops and leaves an edit whose range is
 * the part that actually changed — usually a single number on a `position`
 * line.
 *
 * This is a text-level diff, not a semantic one, and that is fine: both sides
 * come from the same deterministic serializer, so the differing region is
 * already tight.
 */

export interface TextSplice {
	/** Offset of the first differing character. */
	readonly start: number;
	/** Offset just past the last differing character, in the *old* text. */
	readonly end: number;
	readonly text: string;
}

/** `undefined` when the two texts are identical, so the caller can skip the edit. */
export function minimalTextEdit(oldText: string, newText: string): TextSplice | undefined {
	if (oldText === newText) {
		return undefined;
	}

	const limit = Math.min(oldText.length, newText.length);

	let prefix = 0;
	while (prefix < limit && oldText.charCodeAt(prefix) === newText.charCodeAt(prefix)) {
		prefix += 1;
	}

	let suffix = 0;
	while (
		suffix < limit - prefix &&
		oldText.charCodeAt(oldText.length - 1 - suffix) === newText.charCodeAt(newText.length - 1 - suffix)
	) {
		suffix += 1;
	}

	return {
		start: prefix,
		end: oldText.length - suffix,
		text: newText.slice(prefix, newText.length - suffix),
	};
}
