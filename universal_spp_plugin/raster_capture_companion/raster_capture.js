function _readJson(path) {
  var f = alg.fileIO.open(path, "r")
  var text = f.readAll()
  f.close()
  return JSON.parse(text)
}

function _writeJson(path, obj) {
  var f = alg.fileIO.open(path, "w")
  f.write(JSON.stringify(obj, null, 2))
  f.close()
}

function _dir(path) {
  var i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
  return i >= 0 ? path.substring(0, i + 1) : ""
}

function _safe(text) {
  return String(text).replace(/[<>:"\/\\|?*\x00-\x1f]+/g, "_")
}

function _indexDocument() {
  var doc = alg.mapexport.documentStructure()
  var byUid = {}
  var stacks = []

  function visitLayer(layer, material, stack, materialIndex, stackIndex) {
    byUid[String(layer.uid)] = {
      material: material.name,
      stack: stack.name,
      material_index: materialIndex,
      stack_index: stackIndex,
      channels: stack.channels
    }
    if (layer.layers !== undefined) {
      for (var i = 0; i < layer.layers.length; ++i) {
        visitLayer(layer.layers[i], material, stack, materialIndex, stackIndex)
      }
    }
  }

  for (var mi in doc.materials) {
    var material = doc.materials[mi]
    var materialIndex = parseInt(mi, 10)
    for (var si in material.stacks) {
      var stack = material.stacks[si]
      var stackIndex = parseInt(si, 10)
      stacks.push({
        material: material.name,
        stack: stack.name,
        material_index: materialIndex,
        stack_index: stackIndex,
        channels: stack.channels
      })
      for (var li in stack.layers) {
        visitLayer(stack.layers[li], material, stack, materialIndex, stackIndex)
      }
    }
  }
  return {byUid: byUid, stacks: stacks}
}

function _exportConfig(kind, channel) {
  var conf = {
    padding: "Transparent",
    dilation: 0,
    bitDepth: kind === "mask" ? 8 : 8,
    keepAlpha: true
  }
  if (kind !== "mask" && channel) {
    try {
      var fmt = alg.mapexport.channelFormat(channel.path, channel.name)
      conf.bitDepth = Math.min(fmt.bitDepth || 8, 16)
    } catch (e) {
      conf.bitDepth = 8
    }
  }
  return conf
}

function _save(selector, filename, config) {
  alg.mapexport.save(selector, filename, config)
}

function _captureStackChannels(req, index, outDir, assets) {
  for (var s = 0; s < index.stacks.length; ++s) {
    var stack = index.stacks[s]
    for (var c = 0; c < stack.channels.length; ++c) {
      var channel = stack.channels[c]
      var name = _safe(req.id + "_" + stack.material + "_" + stack.stack + "_" + channel + ".png")
      _save([stack.material, stack.stack, channel], outDir + name, _exportConfig("content", {
        path: [stack.material, stack.stack],
        name: channel
      }))
      assets.push({
        request_id: req.id,
        path: name,
        material: stack.material,
        stack: stack.stack,
        material_index: stack.material_index,
        stack_index: stack.stack_index,
        channel_index: c,
        channel: channel,
        kind: "full_stack_channel",
        mime: "image/png"
      })
    }
  }
}

function _captureLayerChannels(req, uid, index, outDir, assets) {
  var entry = index.byUid[String(uid)]
  for (var c = 0; c < entry.channels.length; ++c) {
    var channel = entry.channels[c]
    var chName = _safe(req.id + "_" + channel + ".png")
    _save([uid, channel], outDir + chName, _exportConfig("content", {
      path: [entry.material, entry.stack],
      name: channel
    }))
    assets.push({
      request_id: req.id,
      path: chName,
      material: entry.material,
      stack: entry.stack,
      material_index: entry.material_index,
      stack_index: entry.stack_index,
      channel_index: c,
      channel: channel,
      kind: req.scope === "group" ? "group" : "content",
      mime: "image/png"
    })
  }
}

function capture(planPath, manifestPath) {
  var plan = _readJson(planPath)
  var index = _indexDocument()
  var outDir = _dir(manifestPath)
  var assets = []
  var warnings = []
  var requests = plan.requests || []

  for (var i = 0; i < requests.length; ++i) {
    var req = requests[i]
    var cap = req.capture || {}
    var selector = cap.selector || []
    var uid = selector.length ? selector[0] : req.layer_uid

    try {
      if (req.kind === "mask" && uid !== null && uid !== undefined) {
        var maskName = _safe(req.id + "_mask.png")
        _save([uid, "mask"], outDir + maskName, _exportConfig("mask"))
        assets.push({request_id: req.id, path: maskName, kind: "mask", mime: "image/png"})
      } else if (req.scope === "full_stack_channel") {
        _captureStackChannels(req, index, outDir, assets)
      } else if (uid !== null && uid !== undefined && index.byUid[String(uid)]) {
        _captureLayerChannels(req, uid, index, outDir, assets)
      } else {
        warnings.push("request " + req.id + " had no capturable layer uid")
      }
    } catch (e) {
      warnings.push("failed to capture " + req.id + ": " + e.message)
    }
  }

  _writeJson(manifestPath, {
    version: 1,
    source_plan: planPath,
    requests: requests,
    assets: assets,
    warnings: warnings
  })
  alg.log.info("Universal SPP raster capture wrote " + assets.length + " asset(s)")
}
