// Run with: node --test tests/raster_capture.test.js
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

const source = fs.readFileSync(path.join(__dirname,
  "../universal_spp_plugin/raster_capture_companion/raster_capture.js"), "utf8")

function request(id, material, index, uid, scope = "layer") {
  return {
    id, material_name: material, material_index: index, stack_index: 0,
    layer_uid: uid, kind: "content", scope,
    capture: {selector: [uid, "<channel>"]}
  }
}

function capture(requests, materials = [
  {name: "Body", stacks: [{name: "", channels: ["baseColor"], layers: [{uid: 50}]}]},
  {name: "NewEyes", stacks: [{name: "", channels: ["baseColor"], layers: [{uid: 134}]}]},
  {name: "NewBody", stacks: [{name: "", channels: ["baseColor"], layers: [{uid: 147}]}]}
]) {
  const files = {
    "plan.json": JSON.stringify({requests}),
    "preparation.json": JSON.stringify({added_materials: []}),
    "options.json": "{}"
  }
  const saves = []
  const context = vm.createContext({alg: {
    fileIO: {open: name => ({
      readAll: () => files[name], write: value => { files[name] = value }, close() {}
    })},
    mapexport: {
      documentStructure: () => ({materials}),
      channelFormat: () => ({bitDepth: 8}),
      save: (selector, filename) => saves.push({selector: Array.from(selector), filename})
    },
    log: {info() {}},
    texturesets: {removeChannel() {}}
  }})
  vm.runInContext(source, context)
  context.capture("plan.json", "out/manifest.json", "preparation.json", "options.json")
  return {manifest: JSON.parse(files["out/manifest.json"]), saves}
}

test("two reassigned texture sets do not block capture of the remaining material", () => {
  const {manifest, saves} = capture([
    request("old_body", "OldBody", 0, 24),
    request("old_eyes", "OldEyes", 1, 37),
    request("body", "Body", 2, 50)
  ])
  assert.deepEqual(manifest.warnings, [])
  assert.deepEqual(manifest.skipped_requests, [
    {request_id: "old_body", material_name: "OldBody", reason: "unused_texture_set"},
    {request_id: "old_eyes", material_name: "OldEyes", reason: "unused_texture_set"}
  ])
  assert.deepEqual(saves.map(s => s.selector), [[50, "baseColor"]])
  assert.equal(manifest.assets[0].material_index, 2)
})

test("full stack capture follows the material name after export indexes shift", () => {
  const {manifest, saves} = capture([request("body", "Body", 2, 50, "full_stack_channel")])
  assert.deepEqual(saves.map(s => s.selector), [["Body", "", "baseColor"]])
  assert.equal(manifest.assets[0].material, "Body")
  assert.equal(manifest.assets[0].material_index, 2)
})

test("unused mask, overlay and full stack requests never invoke the exporter", () => {
  const requests = ["mask_stack", "effect_overlay", "full_stack_channel"].map(scope => {
    const req = request(scope, "OldBody", 0, 24, scope)
    req.kind = scope === "mask_stack" ? "mask" : "content"
    return req
  })
  const {manifest, saves} = capture(requests)
  assert.deepEqual(saves, [])
  assert.deepEqual(manifest.warnings, [])
  assert.equal(manifest.skipped_requests.length, 3)
})

test("a missing layer on an active material is still a capture failure", () => {
  const {manifest} = capture([request("missing", "Body", 2, 999)])
  assert.deepEqual(manifest.skipped_requests || [], [])
  assert.deepEqual(manifest.warnings, ["request missing had no capturable layer uid"])
})

test("legacy plans without material names cannot silently skip missing layers", () => {
  const req = request("legacy", undefined, 0, 24)
  const {manifest} = capture([req])
  assert.deepEqual(manifest.skipped_requests || [], [])
  assert.deepEqual(manifest.warnings, ["request legacy had no capturable layer uid"])
})

test("legacy full stack plans keep index-based selection", () => {
  const {saves} = capture([request("legacy", undefined, 1, null, "full_stack_channel")])
  assert.deepEqual(saves.map(s => s.selector), [["NewEyes", "", "baseColor"]])
})
