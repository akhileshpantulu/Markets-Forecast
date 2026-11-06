/// <reference lib="webworker" />
// Web Worker wrapper around the Python engine runtime.
import { createRuntime, type Msg } from "./runtime";

const handle = createRuntime((m) => (self as DedicatedWorkerGlobalScope).postMessage(m));
self.onmessage = (ev: MessageEvent<Msg>) => void handle(ev.data);
