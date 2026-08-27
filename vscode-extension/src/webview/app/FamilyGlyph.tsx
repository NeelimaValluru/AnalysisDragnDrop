/**
 * Small inline glyphs for node families. Bundled SVG, no icon font, no CDN.
 */

import type { ReactElement } from 'react';
import type { NodeFamily } from '../../pipeline/nodeFamily';

export function FamilyGlyph({ family }: { readonly family: NodeFamily }): ReactElement {
	const stroke =
		family === 'neural-eeg' ||
		family === 'neural-lfp' ||
		family === 'neural-spike' ||
		family === 'neural' ||
		family === 'spikeinterface';
	return (
		<svg
			className={`family-glyph${stroke ? ' family-glyph--stroke' : ''}`}
			viewBox="0 0 16 16"
			aria-hidden="true"
			focusable="false"
		>
			{glyphPath(family)}
		</svg>
	);
}

function glyphPath(family: NodeFamily): ReactElement {
	switch (family) {
		case 'loader':
			return <path d="M3 4h10v2H3V4zm0 3h10v7H3V7zm2 2v3h6V9H5z" />;
		case 'preprocessor':
			return <path d="M2 3h12l-4 5v4l-4 2V8L2 3z" />;
		case 'analyzer':
			return <path d="M2 13h12v1H2v-1zm1-2 3-6 3 4 3-5 2 7H3z" />;
		case 'visualizer':
			return (
				<>
					<rect x="2" y="9" width="3" height="5" />
					<rect x="6.5" y="5" width="3" height="9" />
					<rect x="11" y="3" width="3" height="11" />
				</>
			);
		case 'output':
			return <path d="M3 3h7v3h3v7H3V3zm7 0 3 3H10V3z" />;
		case 'llm-claude':
		case 'llm-gpt':
		case 'llm':
			return <path d="M8 2 9.8 6.2 14 8l-4.2 1.8L8 14l-1.8-4.2L2 8l4.2-1.8L8 2z" />;
		case 'neural-eeg':
			return <path d="M1 8c2-4 3 4 5 0s3 4 5 0 3 4 4 0" fill="none" stroke="currentColor" strokeWidth="1.4" />;
		case 'neural-lfp':
			return <path d="M1 8c3-2 4 2 7 0s4 2 7 0" fill="none" stroke="currentColor" strokeWidth="1.4" />;
		case 'neural-spike':
			return <path d="M1 10h3l1-6 2 10 2-8 1 4h5" fill="none" stroke="currentColor" strokeWidth="1.4" />;
		case 'neural-calcium':
			return (
				<>
					<circle cx="5" cy="8" r="2" />
					<circle cx="10" cy="6" r="1.5" />
					<circle cx="12" cy="11" r="1.2" />
				</>
			);
		case 'neural':
			return <path d="M2 8c2-3 3 3 5 0s3 3 5 0 2 3 2 0" fill="none" stroke="currentColor" strokeWidth="1.4" />;
		case 'spikeinterface':
			return (
				<>
					<path d="M4 2h8l2 4-6 8L2 6l2-4z" fill="none" stroke="currentColor" strokeWidth="1.2" />
					<path d="M4 6h8" fill="none" stroke="currentColor" strokeWidth="1.2" />
				</>
			);
		case 'custom':
			return <path d="M6 3 3 8l3 5h1L4 8l3-5H6zm4 0h1l3 5-3 5h-1l3-5-3-5z" />;
		default:
			return <rect x="3" y="3" width="10" height="10" rx="1" />;
	}
}
