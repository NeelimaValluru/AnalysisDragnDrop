/**
 * The node palette, built entirely from `describe --json`.
 *
 * Kinds are grouped by visual family (Neural, SpikeInterface, Analysis, Models,
 * Your code) rather than the raw `node_type` string, so SpikeInterface sits on
 * its own rail and discovered library functions land under Your code. Search
 * filters the same list; grouping still follows `describe` order inside each
 * rail.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { classifyKind, groupPalette, kindMatchesQuery } from '../../pipeline/nodeFamily';
import { paletteKinds } from '../../pipeline/nodeKinds';
import type { NodeKind, NodeKindRegistry } from '../../pipeline/nodeKinds';
import { FamilyGlyph } from './FamilyGlyph';

export const PALETTE_DRAG_MIME = 'application/x-analysis-gui-node-kind';

export interface PaletteProps {
	readonly registry: NodeKindRegistry;
	readonly disabled: boolean;
	readonly onAdd: (kind: NodeKind) => void;
}

export function Palette({ registry, disabled, onAdd }: PaletteProps): ReactElement {
	const kinds = paletteKinds(registry);
	const [query, setQuery] = useState('');
	const searchRef = useRef<HTMLInputElement>(null);

	const groups = useMemo(() => {
		const visible = kinds.filter((kind) => kindMatchesQuery(kind, query));
		return groupPalette(visible);
	}, [kinds, query]);

	useEffect(() => {
		const onKey = (event: KeyboardEvent): void => {
			if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) {
				return;
			}
			const target = event.target;
			if (
				target instanceof HTMLInputElement ||
				target instanceof HTMLTextAreaElement ||
				target instanceof HTMLSelectElement
			) {
				return;
			}
			event.preventDefault();
			searchRef.current?.focus();
		};
		window.addEventListener('keydown', onKey);
		return () => {
			window.removeEventListener('keydown', onKey);
		};
	}, []);

	return (
		<aside className="palette" aria-label="Node palette">
			<h2 className="panel__title">Nodes</h2>
			{kinds.length === 0 ? (
				<p className="panel__empty">
					No node kinds are available. The extension could not read <code>describe --json</code> from the
					Python CLI — check the Analysis GUI output channel.
				</p>
			) : (
				<>
					<label className="palette__search">
						<span className="palette__search-label">Search</span>
						<input
							ref={searchRef}
							type="search"
							className="palette__search-input"
							placeholder="Filter nodes"
							value={query}
							disabled={disabled}
							onChange={(event) => {
								setQuery(event.target.value);
							}}
							onKeyDown={(event: ReactKeyboardEvent<HTMLInputElement>) => {
								if (event.key === 'Escape') {
									setQuery('');
									event.currentTarget.blur();
								}
							}}
						/>
					</label>
					{groups.length === 0 ? (
						<p className="panel__empty">No nodes match “{query}”.</p>
					) : (
						groups.map((group) => (
							<details
								key={group.id}
								className={`palette__group${group.id === 'spikeinterface' ? ' palette__group--si' : ''}`}
								open
							>
								<summary className="palette__group-title">
									{group.title}
									<span className="palette__group-count">{group.kinds.length}</span>
								</summary>
								{group.kinds.map((kind) => (
									<PaletteItem key={kind.kind} kind={kind} disabled={disabled} onAdd={onAdd} />
								))}
							</details>
						))
					)}
				</>
			)}
			<p className="panel__hint">Drag onto the canvas, or click to drop one in the middle. / focuses search.</p>
		</aside>
	);
}

function PaletteItem({
	kind,
	disabled,
	onAdd,
}: {
	readonly kind: NodeKind;
	readonly disabled: boolean;
	readonly onAdd: (kind: NodeKind) => void;
}): ReactElement {
	const appearance = classifyKind(kind);
	return (
		<button
			type="button"
			className={`palette__item family-${appearance.family}`}
			disabled={disabled}
			title={kind.description}
			draggable={!disabled}
			onDragStart={(event) => {
				event.dataTransfer.setData(PALETTE_DRAG_MIME, kind.kind);
				event.dataTransfer.effectAllowed = 'copy';
			}}
			onClick={() => {
				onAdd(kind);
			}}
		>
			<FamilyGlyph family={appearance.family} />
			<span className="palette__item-text">
				<span className="palette__item-label">{kind.palette_label}</span>
				<span className="palette__item-hint">{kind.description}</span>
			</span>
		</button>
	);
}
