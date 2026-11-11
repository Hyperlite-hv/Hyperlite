import { createContext } from "react";

// True inside the cluster's frame: its header already carries the page's h1, so the page header steps down a level.
export const FrameContext = createContext(false);
