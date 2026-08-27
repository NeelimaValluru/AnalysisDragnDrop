/**
 * The React Flow generic instantiations, in one place.
 *
 * React Flow v12 is generic over node and edge types, and every hook and
 * callback has to agree on the same instantiation or the inference collapses
 * into `any`. Naming them once here is what keeps `strict` meaningful across
 * the component tree.
 */

import type { Edge, Node } from '@xyflow/react';
import type { FlowEdgeData, FlowNodeData } from '../../pipeline/graphModel';

export type PipelineFlowNode = Node<FlowNodeData, 'pipelineNode'>;
export type PipelineFlowEdge = Edge<FlowEdgeData>;
