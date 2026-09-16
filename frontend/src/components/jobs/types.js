/**
 * Runtime constants carried over from the VisRax frontend's `api/types.ts`.
 * The TypeScript interfaces there are compile-time only, so only the `as const`
 * arrays survive the port to plain JS.
 */

export const URL_TYPES = ["rtsp", "video"];

/** Backend quirk: the line region type is capital-L "Line" on the wire. */
export const REGION_TYPES = ["polygon", "bounding_box", "Line"];

export const MOVING_DIRECTIONS = [
    "up_to_down",
    "down_to_up",
    "left_to_right",
    "right_to_left",
];
