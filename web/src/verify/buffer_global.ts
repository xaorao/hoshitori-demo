// The verifier's source uses Node's Buffer. A page has none, so the `buffer` package (the same API, MIT) stands in; this module is
// imported before the verifier's, so the global exists when they load. In Node the global is already there and is left alone.
import { Buffer as PageBuffer } from "buffer";

const scope = globalThis as { Buffer?: unknown };
if (scope.Buffer === undefined) scope.Buffer = PageBuffer;
