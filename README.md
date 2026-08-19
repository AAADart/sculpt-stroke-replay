# Sculpt Stroke Replay
<img width="512" height="512" alt="sculpt_stroke_replay" src="https://github.com/user-attachments/assets/a45452cf-3f09-431f-99c4-e8ffd3e5b829" />

Replay the last sculpt stroke and adjust its strength with live preview.

Now supports both regular sculpt meshes and **Multiresolution sculpting** while keeping the same workflow.

## Features

- Replay the last sculpt stroke
- Live strength preview
- Positive and negative strength values
- Multiresolution sculpting support
- Supports different Multires Viewport and Sculpt levels
- Alt + Shift + R shortcut
- Sculpt Mode sidebar panel

## Usage
<img width="800" height="620" alt="ezgif-4c381a62effaa28b" src="https://github.com/user-attachments/assets/4264c1a2-bd39-44d8-aef8-9c63316bda44" />

1. Go to Sculpt Mode.
2. Make a sculpt stroke.
3. Press **Alt + Shift + R**.
4. Adjust **Repeat Strength**:
   - `1.0` = replay once
   - `0.5` = add half strength
   - `2.0` = double the effect
   - `-0.5` = partially reduce the stroke
   - `0.0` = return to the original stroke result
5. Press **OK** to confirm, or **Cancel** to restore the original stroke result.

## Multiresolution

Sculpt Stroke Replay can replay strokes made on a **Multiresolution** mesh using the same `Alt + Shift + R` workflow.

It also supports the case where the Multires **Sculpt** level is higher than the **Viewport** level, for example after adding another subdivision while already in Sculpt Mode.

The add-on returns you to Sculpt Mode after replay and keeps its temporary Multires helper object out of the scene.

## Limitations

Currently not supported:

- Dynamic Topology (Dyntopo)
- Operations that change topology between undo states

## Notes

This add-on uses Blender's undo/redo history to reconstruct the last sculpt stroke.

For Multires, it temporarily evaluates the sculpted surface at the active Sculpt level and writes the adjusted result back through Blender's Multires reshape workflow.

For best results, use it immediately after making a sculpt brush stroke.
