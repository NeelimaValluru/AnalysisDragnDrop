/**
 * One node on the canvas.
 *
 * A row per port, with the handle sitting on the card edge beside its label, so
 * "y_train goes here" is readable rather than a matter of counting dots. React
 * Flow measures handle positions from the DOM, so laying them out with ordinary
 * flow positioning is all that is required.
 */

import type { ReactElement } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import { inputHandleId, outputHandleId } from '../../pipeline/graphModel';
import type { NodePort } from '../../pipeline/nodeKinds';
import { FamilyGlyph } from './FamilyGlyph';
import type { PipelineFlowNode } from './flowTypes';

export function PipelineNodeCard({ data, selected }: NodeProps<PipelineFlowNode>): ReactElement {
	const className = [
		'pipeline-node',
		`family-${data.family}`,
		selected ? 'is-selected' : '',
		data.severity ? `has-${data.severity}` : '',
		data.runState ? `is-run-${data.runState}` : '',
		data.kindLabel ? '' : 'is-unknown-kind',
		data.family === 'spikeinterface' ? 'is-si' : '',
	]
		.filter(Boolean)
		.join(' ');

	return (
		<div
			className={className}
			data-node-type={data.nodeType}
			data-family={data.family}
			data-signal={data.signalType ?? ''}
			data-provider={data.provider ?? ''}
			aria-busy={data.runState === 'running'}
		>
			<header className="pipeline-node__header">
				<FamilyGlyph family={data.family} />
				<div className="pipeline-node__heading">
					<span className="pipeline-node__title">{data.label}</span>
					<span className="pipeline-node__kind">{data.kindLabel ?? (data.nodeType || 'unknown kind')}</span>
				</div>
				<span className="pipeline-node__badge">{data.badge}</span>
				{data.runState && data.runState !== 'pending' && (
					<span
						className={`pipeline-node__run pipeline-node__run--${data.runState}`}
						title={runStateTitle(data.runState)}
					>
						{data.runState === 'running' ? '…' : data.runState === 'ok' ? '✓' : '✕'}
					</span>
				)}
				{data.severity && (
					<span
						className={`pipeline-node__mark pipeline-node__mark--${data.severity}`}
						title={data.severity === 'error' ? 'This node has errors' : 'This node has warnings'}
					>
						{data.severity === 'error' ? '!' : '!'}
					</span>
				)}
			</header>

			<div className="pipeline-node__ports">
				<ul className="pipeline-node__side">
					{data.inputs.map((port) => (
						<PortRow key={port.name} port={port} side="input" />
					))}
				</ul>
				<ul className="pipeline-node__side pipeline-node__side--out">
					{data.outputs.map((port) => (
						<PortRow key={port.name} port={port} side="output" />
					))}
				</ul>
			</div>

			{(data.preview || data.overrides.length > 0) && (
				<footer className="pipeline-node__overrides">
					{data.preview && (
						<span className="pipeline-node__preview">
							{data.preview.name}: {data.preview.value}
						</span>
					)}
				</footer>
			)}
		</div>
	);
}

function runStateTitle(state: 'running' | 'ok' | 'error'): string {
	if (state === 'running') {
		return 'This node is running';
	}
	if (state === 'ok') {
		return 'This node finished';
	}
	return 'This node failed';
}

function PortRow({ port, side }: { port: NodePort; side: 'input' | 'output' }): ReactElement {
	const isInput = side === 'input';
	return (
		<li className="pipeline-port" title={`${port.label} — ${port.data_kind}${port.description ? `\n${port.description}` : ''}`}>
			<Handle
				type={isInput ? 'target' : 'source'}
				position={isInput ? Position.Left : Position.Right}
				id={isInput ? inputHandleId(port.name) : outputHandleId(port.name)}
				className={`pipeline-port__handle kind-${port.data_kind}`}
			/>
			<span className="pipeline-port__label">
				{port.label}
				{port.required && (
					<span className="pipeline-port__required" title="Required input">
						{' '}
						*
					</span>
				)}
				<span className={`pipeline-port__kind kind-${port.data_kind}`}>{port.data_kind}</span>
			</span>
		</li>
	);
}
