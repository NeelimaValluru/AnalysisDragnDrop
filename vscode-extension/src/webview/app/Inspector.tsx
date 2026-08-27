/**
 * The parameter inspector for the selected node.
 *
 * Every edit writes `value` and never `default_value`. The default belongs to
 * the node kind — it is whatever `describe` declared — and `resolved_value` in
 * Python reads the override when it is set and the default otherwise. So
 * "Reset" here means writing `null`, not copying the default in: copying it
 * would pin the node to today's default forever and make an untouched
 * parameter indistinguishable from a deliberate one.
 *
 * Text-ish inputs commit on blur or Enter rather than on every keystroke. Each
 * commit is one intent and therefore one undo step, and a per-keystroke commit
 * would bury Ctrl+Z the same way a per-mouse-move drag would.
 */

import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import type { JsonValue, PipelineNode, PipelineParameter } from '../../pipeline/document';
import { classifyNode, inspectorParamGroups } from '../../pipeline/nodeFamily';
import type { NodeKind } from '../../pipeline/nodeKinds';
import { FamilyGlyph } from './FamilyGlyph';

export interface InspectorProps {
	readonly node: PipelineNode | undefined;
	readonly kind: NodeKind | undefined;
	readonly disabled: boolean;
	readonly onSetParam: (nodeId: string, param: string, value: JsonValue) => void;
	readonly onDelete: (nodeId: string) => void;
}

export function Inspector({ node, kind, disabled, onSetParam, onDelete }: InspectorProps): ReactElement {
	if (!node) {
		return (
			<aside className="inspector" aria-label="Node inspector">
				<h2 className="panel__title">Inspector</h2>
				<p className="panel__empty">Select a node to edit its parameters.</p>
			</aside>
		);
	}

	const parameters = [...node.parameters.values()];
	const appearance = classifyNode(node, kind);
	const groups = inspectorParamGroups(parameters);
	const description = node.description || kind?.description || '';

	return (
		<aside className="inspector" aria-label="Node inspector">
			<header className={`inspector__head family-${appearance.family}`}>
				<FamilyGlyph family={appearance.family} />
				<div>
					<h2 className="panel__title inspector__title">{node.label || node.id}</h2>
					<span className="inspector__badge">{appearance.badge}</span>
				</div>
			</header>

			<dl className="inspector__meta">
				<dt>Kind</dt>
				<dd>{kind?.palette_label ?? <em>not in the registry</em>}</dd>
				<dt>Type</dt>
				<dd>
					<code>{node.node_type || '—'}</code>
				</dd>
				{appearance.signalType && (
					<>
						<dt>Signal</dt>
						<dd>{appearance.signalType.toUpperCase()}</dd>
					</>
				)}
				{appearance.provider && (
					<>
						<dt>Provider</dt>
						<dd>{appearance.provider}</dd>
					</>
				)}
				{appearance.siStage && (
					<>
						<dt>SI stage</dt>
						<dd>{appearance.siStage}</dd>
					</>
				)}
				<dt>Id</dt>
				<dd>
					<code className="inspector__id">{node.id}</code>
				</dd>
			</dl>

			{description && <p className="inspector__description">{description}</p>}

			{parameters.length === 0 ? (
				<p className="panel__empty">This node kind has no parameters.</p>
			) : (
				<div className="inspector__params">
					{groups.map((group) => (
						<section key={group.id} className="inspector__group">
							<h3 className="inspector__group-title">{group.title}</h3>
							{group.parameters.map((parameter) => (
								<ParameterField
									key={`${node.id}:${parameter.name}`}
									parameter={parameter}
									disabled={disabled}
									onCommit={(value) => {
										onSetParam(node.id, parameter.name, value);
									}}
								/>
							))}
						</section>
					))}
				</div>
			)}

			<button
				type="button"
				className="inspector__delete"
				disabled={disabled}
				onClick={() => {
					onDelete(node.id);
				}}
			>
				Delete node
			</button>
		</aside>
	);
}

interface ParameterFieldProps {
	readonly parameter: PipelineParameter;
	readonly disabled: boolean;
	readonly onCommit: (value: JsonValue) => void;
}

function ParameterField({ parameter, disabled, onCommit }: ParameterFieldProps): ReactElement {
	const overridden = parameter.value !== null;
	const effective = parameter.value ?? parameter.default_value;

	return (
		<label className={`param${overridden ? ' is-overridden' : ''}`}>
			<span className="param__name">
				{parameter.name}
				{overridden && (
					<button
						type="button"
						className="param__reset"
						disabled={disabled}
						title="Clear the override and fall back to the default"
						onClick={(event) => {
							event.preventDefault();
							onCommit(null);
						}}
					>
						Reset
					</button>
				)}
			</span>
			<ParameterControl
				parameter={parameter}
				effective={effective}
				disabled={disabled}
				onCommit={onCommit}
			/>
			{parameter.description && <span className="param__hint">{parameter.description}</span>}
			<span className="param__default">
				default: <code>{formatJson(parameter.default_value)}</code>
			</span>
		</label>
	);
}

interface ParameterControlProps extends ParameterFieldProps {
	readonly effective: JsonValue;
}

/**
 * Picks a widget from `param_type`.
 *
 * The vocabulary is the CLI's, not ours — an unrecognised type falls through to
 * a text box rather than refusing to render, so a parameter type added in
 * Python is editable here on the day it ships, just without a bespoke control.
 */
function ParameterControl({ parameter, effective, disabled, onCommit }: ParameterControlProps): ReactElement {
	switch (parameter.param_type) {
		case 'boolean':
			return (
				<input
					type="checkbox"
					className="param__checkbox"
					disabled={disabled}
					checked={effective === true}
					onChange={(event) => {
						onCommit(event.target.checked);
					}}
				/>
			);

		case 'dropdown':
			return (
				<select
					className="param__control"
					disabled={disabled}
					value={typeof effective === 'string' ? effective : ''}
					onChange={(event) => {
						onCommit(event.target.value);
					}}
				>
					{!parameter.options.some((option) => option === effective) && (
						<option value="">{formatJson(effective)}</option>
					)}
					{parameter.options.map((option) => (
						<option key={formatJson(option)} value={formatJson(option)}>
							{formatJson(option)}
						</option>
					))}
				</select>
			);

		case 'number':
			return (
				<DraftInput
					type="number"
					disabled={disabled}
					value={effective === null ? '' : formatJson(effective)}
					onCommit={(text) => {
						const trimmed = text.trim();
						if (trimmed === '') {
							onCommit(null);
							return;
						}
						const parsed = Number(trimmed);
						// A number box can still hold something unparseable in
						// some browsers; refusing to write NaN into the document
						// is cheaper than validating it back out later.
						if (Number.isFinite(parsed)) {
							onCommit(parsed);
						}
					}}
				/>
			);

		default:
			return (
				<DraftInput
					type="text"
					disabled={disabled}
					placeholder={parameter.param_type === 'file' ? 'path to a file' : undefined}
					value={typeof effective === 'string' ? effective : effective === null ? '' : formatJson(effective)}
					onCommit={(text) => {
						onCommit(text === '' ? null : text);
					}}
				/>
			);
	}
}

interface DraftInputProps {
	readonly type: 'text' | 'number';
	readonly value: string;
	readonly disabled: boolean;
	readonly placeholder?: string | undefined;
	readonly onCommit: (value: string) => void;
}

/**
 * A controlled input that keeps its own draft and reports on blur or Enter.
 *
 * The draft is re-seeded whenever the committed value changes underneath it,
 * which is what makes an edit to the same parameter in a text editor show up
 * here instead of being masked by a stale keystroke.
 */
function DraftInput({ type, value, disabled, placeholder, onCommit }: DraftInputProps): ReactElement {
	const [draft, setDraft] = useState(value);

	useEffect(() => {
		setDraft(value);
	}, [value]);

	return (
		<input
			type={type}
			className="param__control"
			disabled={disabled}
			placeholder={placeholder}
			value={draft}
			onChange={(event) => {
				setDraft(event.target.value);
			}}
			onBlur={() => {
				if (draft !== value) {
					onCommit(draft);
				}
			}}
			onKeyDown={(event) => {
				if (event.key === 'Enter') {
					event.currentTarget.blur();
				} else if (event.key === 'Escape') {
					setDraft(value);
				}
			}}
		/>
	);
}

function formatJson(value: JsonValue): string {
	return value === null ? 'null' : typeof value === 'string' ? value : JSON.stringify(value);
}
