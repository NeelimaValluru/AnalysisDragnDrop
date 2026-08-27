/**
 * The canvas.
 *
 * This component is the whole portable application. Its interface is a plain
 * one — a document and a node-kind registry in, intents out — and it imports
 * nothing from `vscode` and nothing that knows a webview exists. Hosting it
 * somewhere else means writing the twenty lines that answer `onIntent` and push
 * a new `document` prop; a browser page or a Jupyter widget would each be that
 * and no more. `../index.tsx` is the VS Code answer to the same interface.
 *
 * ## The optimistic replica
 *
 * The host echo-guards: an edit the canvas caused does not come back as a
 * `document` message, because bouncing it would reset positions under the
 * pointer and reformat the file out from under a text editor open on the same
 * document. So the canvas has to move the node itself.
 *
 * It does that by holding a local copy of the document and running the *same*
 * `applyIntent` the host runs. Both sides therefore compute the same next
 * document from the same input by construction rather than by agreement, and
 * the only thing that can put them out of step — an external edit — arrives as
 * a `document` prop and replaces the replica outright.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import {
	Background,
	BackgroundVariant,
	ConnectionLineType,
	Controls,
	MiniMap,
	Panel,
	ReactFlow,
	ReactFlowProvider,
	useEdgesState,
	useNodesState,
	useReactFlow,
} from '@xyflow/react';
import type { Connection, IsValidConnection, Viewport } from '@xyflow/react';
import type { JsonValue, PipelineDocument } from '../../pipeline/document';
import { nodeToJson } from '../../pipeline/document';
import { applyIntent, type PipelineIntent } from '../../pipeline/intents';
import { canConnect, refFromConnection, toFlowModel, type NodeSeverity } from '../../pipeline/graphModel';
import {
	availableStarters,
	FAMILY_SWATCH,
	starterIntents,
	type StarterChip,
} from '../../pipeline/nodeFamily';
import { matchKind, nodeFromKind, uuid4, type NodeKind, type NodeKindRegistry } from '../../pipeline/nodeKinds';
import type { CanvasViewState, RunNodeState } from '../../pipeline/protocol';
import { Inspector } from './Inspector';
import { PALETTE_DRAG_MIME, Palette } from './Palette';
import { PipelineNodeCard } from './PipelineNodeCard';
import type { PipelineFlowEdge, PipelineFlowNode } from './flowTypes';

export interface PipelineCanvasProps {
	/** The document to render, or `undefined` before the first one arrives. */
	readonly document: PipelineDocument | undefined;
	/** Set when the backing text is not parseable right now. */
	readonly parseError: string | undefined;
	readonly registry: NodeKindRegistry;
	readonly marks: ReadonlyMap<string, NodeSeverity>;
	readonly runStates: ReadonlyMap<string, RunNodeState>;
	readonly initialViewState: CanvasViewState;
	readonly onIntent: (intent: PipelineIntent) => void;
	readonly onViewStateChange: (state: CanvasViewState) => void;
}

const NODE_TYPES = { pipelineNode: PipelineNodeCard };

/** Long enough to read, short enough not to sit in the way of the next drag. */
const REJECTION_TOAST_MS = 4000;
const REJECT_FLASH_MS = 420;

export function PipelineCanvas(props: PipelineCanvasProps): ReactElement {
	return (
		<ReactFlowProvider>
			<CanvasBody {...props} />
		</ReactFlowProvider>
	);
}

function CanvasBody({
	document,
	parseError,
	registry,
	marks,
	runStates,
	initialViewState,
	onIntent,
	onViewStateChange,
}: PipelineCanvasProps): ReactElement {
	const { screenToFlowPosition } = useReactFlow<PipelineFlowNode, PipelineFlowEdge>();

	const [replica, setReplica] = useState<PipelineDocument | undefined>(document);
	const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>(initialViewState.selectedNodeId);
	const [rejection, setRejection] = useState<string | undefined>(undefined);
	const [rejectFlash, setRejectFlash] = useState(false);

	// An external edit — someone typing in a text editor on the same file —
	// is the only thing that changes this prop, so replacing the replica
	// wholesale is correct and cannot fight with a drag in progress.
	useEffect(() => {
		setReplica(document);
	}, [document]);

	const model = useMemo(() => toFlowModel(replica ?? EMPTY_DOCUMENT, registry, marks), [replica, registry, marks]);

	const [nodes, setNodes, onNodesChange] = useNodesState<PipelineFlowNode>([]);
	const [edges, setEdges, onEdgesChange] = useEdgesState<PipelineFlowEdge>([]);

	// Re-seed React Flow from the model, keeping whatever selection was in
	// play. Selection is view state; losing it because a parameter changed
	// would empty the inspector mid-edit.
	useEffect(() => {
		setNodes((previous) => {
			const selected = new Set(previous.filter((node) => node.selected).map((node) => node.id));
			return model.nodes.map((node) => ({
				id: node.id,
				type: node.type,
				position: { x: node.position.x, y: node.position.y },
				data: { ...node.data, runState: runStates.get(node.id) },
				selected: selected.has(node.id),
			}));
		});
	}, [model.nodes, runStates, setNodes]);

	useEffect(() => {
		setEdges((previous) => {
			const selected = new Set(previous.filter((edge) => edge.selected).map((edge) => edge.id));
			return model.edges.map((edge) => ({
				id: edge.id,
				source: edge.source,
				target: edge.target,
				sourceHandle: edge.sourceHandle,
				targetHandle: edge.targetHandle,
				data: edge.data,
				className: ['pipeline-edge', `kind-${edge.data.dataKind ?? 'any'}`, edgeRunClass(edge, runStates)]
					.filter(Boolean)
					.join(' '),
				selected: selected.has(edge.id),
			}));
		});
	}, [model.edges, runStates, setEdges]);

	// A ref, not the state value, so callbacks handed to React Flow do not have
	// to be rebuilt (and re-registered) on every document change.
	const replicaRef = useRef(replica);
	replicaRef.current = replica;

	const emit = useCallback(
		(intent: PipelineIntent) => {
			const current = replicaRef.current;
			if (!current) {
				return;
			}
			const next = applyIntent(current, intent);
			if (!next) {
				// Nothing would change. Sending it anyway would cost an
				// undo step for a gesture that did nothing.
				return;
			}
			// Keep the ref in step inside this turn so a starter template can
			// emit several intents without waiting for a re-render.
			replicaRef.current = next;
			setReplica(next);
			onIntent(intent);
		},
		[onIntent],
	);

	const reject = useCallback((reason: string) => {
		setRejection(reason);
		setRejectFlash(true);
	}, []);

	useEffect(() => {
		if (!rejectFlash) {
			return;
		}
		const timer = setTimeout(() => {
			setRejectFlash(false);
		}, REJECT_FLASH_MS);
		return () => {
			clearTimeout(timer);
		};
	}, [rejectFlash]);

	useEffect(() => {
		if (rejection === undefined) {
			return;
		}
		const timer = setTimeout(() => {
			setRejection(undefined);
		}, REJECTION_TOAST_MS);
		return () => {
			clearTimeout(timer);
		};
	}, [rejection]);

	const addKindAt = useCallback(
		(kind: NodeKind, position: { x: number; y: number }) => {
			const id = uuid4();
			const node = nodeFromKind(kind, id, [Math.round(position.x), Math.round(position.y)]);
			emit({ kind: 'addNode', nodeId: id, node: nodeToJson(node) });
			setSelectedNodeId(id);
		},
		[emit],
	);

	const applyStarter = useCallback(
		(chip: StarterChip) => {
			const origin = screenToFlowPosition({ x: window.innerWidth / 2 - 200, y: window.innerHeight / 2 - 40 });
			const intents = starterIntents(chip, registry, origin);
			if (!intents) {
				return;
			}
			for (const intent of intents) {
				emit(intent);
			}
			const first = intents.find((intent) => intent.kind === 'addNode');
			if (first && first.kind === 'addNode') {
				setSelectedNodeId(first.nodeId);
			}
		},
		[emit, registry, screenToFlowPosition],
	);

	const isValidConnection = useCallback<IsValidConnection<PipelineFlowEdge>>(
		(connection) => {
			const current = replicaRef.current;
			return current
				? canConnect(current, registry, {
						source: connection.source,
						sourceHandle: connection.sourceHandle ?? null,
						target: connection.target,
						targetHandle: connection.targetHandle ?? null,
					}).ok
				: false;
		},
		[registry],
	);

	const onConnect = useCallback(
		(connection: Connection) => {
			const current = replicaRef.current;
			if (!current) {
				return;
			}
			const candidate = {
				source: connection.source,
				sourceHandle: connection.sourceHandle ?? null,
				target: connection.target,
				targetHandle: connection.targetHandle ?? null,
			};
			const verdict = canConnect(current, registry, candidate);
			if (!verdict.ok) {
				reject(verdict.reason);
				return;
			}
			const ref = refFromConnection(candidate);
			if (ref) {
				emit({ kind: 'addEdge', edge: ref });
			}
		},
		[emit, registry, reject],
	);

	const selectedNode = selectedNodeId ? replica?.nodes.get(selectedNodeId) : undefined;
	// While the text does not parse there is nothing to apply an intent to, so
	// the canvas goes read-only rather than queueing edits against a document
	// the host cannot reconstruct.
	const readOnly = replica === undefined || parseError !== undefined;
	const empty = (replica?.nodes.size ?? 0) === 0 && parseError === undefined;
	const starters = useMemo(() => availableStarters(registry), [registry]);

	return (
		<div className="canvas">
			<Palette
				registry={registry}
				disabled={readOnly}
				onAdd={(kind) => {
					// Clicking rather than dragging: drop it in the middle of
					// whatever is currently on screen.
					addKindAt(kind, screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }));
				}}
			/>

			<div
				className={`canvas__surface${parseError ? ' is-stale' : ''}${rejectFlash ? ' is-rejecting' : ''}`}
				onDragOver={(event) => {
					if (event.dataTransfer.types.includes(PALETTE_DRAG_MIME)) {
						event.preventDefault();
						event.dataTransfer.dropEffect = 'copy';
					}
				}}
				onDrop={(event) => {
					const kindName = event.dataTransfer.getData(PALETTE_DRAG_MIME);
					const kind = registry.kinds.find((candidate) => candidate.kind === kindName);
					if (!kind) {
						return;
					}
					event.preventDefault();
					addKindAt(kind, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
				}}
			>
				<ReactFlow<PipelineFlowNode, PipelineFlowEdge>
					nodes={nodes}
					edges={edges}
					nodeTypes={NODE_TYPES}
					onNodesChange={onNodesChange}
					onEdgesChange={onEdgesChange}
					onConnect={onConnect}
					onConnectEnd={(_event, state) => {
						if ('isValid' in state && state.isValid === false) {
							setRejectFlash(true);
						}
					}}
					isValidConnection={isValidConnection}
					// One edit on drag *end*. React Flow reports every mouse
					// move through `onNodesChange`, which is what keeps the node
					// under the pointer; committing those would fill the undo
					// stack with one-pixel moves.
					onNodeDragStop={(_event, _node, dragged) => {
						for (const node of dragged) {
							emit({ kind: 'moveNode', nodeId: node.id, position: [node.position.x, node.position.y] });
						}
					}}
					onNodesDelete={(deleted) => {
						for (const node of deleted) {
							emit({ kind: 'deleteNode', nodeId: node.id });
						}
					}}
					onEdgesDelete={(deleted) => {
						for (const edge of deleted) {
							if (edge.data) {
								emit({ kind: 'deleteEdge', edge: edge.data.ref });
							}
						}
					}}
					onSelectionChange={({ nodes: selected }) => {
						setSelectedNodeId(selected.length === 1 ? selected[0]?.id : undefined);
					}}
					onMoveEnd={(_event, viewport: Viewport) => {
						onViewStateChange({ viewport, selectedNodeId, paletteOpen: true });
					}}
					defaultViewport={initialViewState.viewport}
					fitView={initialViewState.viewport === undefined}
					nodesConnectable={!readOnly}
					nodesDraggable={!readOnly}
					elementsSelectable
					connectionLineType={ConnectionLineType.SmoothStep}
					defaultEdgeOptions={{ type: 'smoothstep' }}
					proOptions={{ hideAttribution: false }}
					minZoom={0.1}
					maxZoom={2}
				>
					<Background
						variant={BackgroundVariant.Dots}
						gap={22}
						size={1.2}
						color="var(--grid-dot)"
					/>
					<Controls showInteractive={false} />
					<MiniMap<PipelineFlowNode>
						pannable
						zoomable
						nodeColor={(node) => FAMILY_SWATCH[node.data.family] ?? FAMILY_SWATCH.unknown}
						maskColor="var(--minimap-mask)"
					/>

					{empty && !readOnly && (
						<Panel position="top-center">
							<div className="empty-state">
								<p className="empty-state__prompt">Drop a node from the palette, or start from a small template.</p>
								{starters.length > 0 && (
									<div className="empty-state__chips">
										{starters.map((chip) => (
											<button
												key={chip.id}
												type="button"
												className="empty-state__chip"
												title={chip.hint}
												onClick={() => {
													applyStarter(chip);
												}}
											>
												<span className="empty-state__chip-label">{chip.label}</span>
												<span className="empty-state__chip-hint">{chip.hint}</span>
											</button>
										))}
									</div>
								)}
							</div>
						</Panel>
					)}

					{parseError && (
						<Panel position="top-center">
							<div className="banner banner--error" role="alert">
								<strong>This file is not valid JSON right now.</strong>
								<span>{parseError}</span>
								<span className="banner__hint">
									The graph below is the last version that parsed. Editing is disabled until the text
									is valid again.
								</span>
							</div>
						</Panel>
					)}

					{!parseError && model.undrawableEdges > 0 && (
						<Panel position="top-center">
							<div className="banner banner--warning" role="status">
								{model.undrawableEdges === 1
									? '1 edge in this file cannot be drawn: it names a node that is not here, or is not an edge.'
									: `${String(model.undrawableEdges)} edges in this file cannot be drawn: they name nodes that are not here, or are not edges.`}
							</div>
						</Panel>
					)}

					{rejection && (
						<Panel position="bottom-center">
							<div className="banner banner--warning" role="alert">
								{rejection}
							</div>
						</Panel>
					)}
				</ReactFlow>
			</div>

			<Inspector
				node={selectedNode}
				kind={selectedNode ? matchKind(registry, selectedNode) : undefined}
				disabled={readOnly}
				onSetParam={(nodeId, param, value: JsonValue) => {
					emit({ kind: 'setParam', nodeId, param, value });
				}}
				onDelete={(nodeId) => {
					emit({ kind: 'deleteNode', nodeId });
					setSelectedNodeId(undefined);
				}}
			/>
		</div>
	);
}

const EMPTY_DOCUMENT: PipelineDocument = {
	version: undefined,
	nodes: new Map(),
	edges: [],
	extra: {},
};

function edgeRunClass(
	edge: { readonly source: string; readonly target: string },
	runStates: ReadonlyMap<string, RunNodeState>,
): string {
	if (runStates.size === 0) {
		return '';
	}
	const source = runStates.get(edge.source);
	const target = runStates.get(edge.target);
	if (source === 'running' || target === 'running') {
		return 'is-run-active';
	}
	if (source === 'ok' && target === 'pending') {
		return 'is-run-active';
	}
	return '';
}
