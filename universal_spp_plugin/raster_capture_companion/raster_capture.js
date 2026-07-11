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

function _pathIndexAfter(req, marker) {
  var path = req.path || []
  for (var i = 0; i < path.length - 1; ++i) {
    if (path[i] === marker) {
      var m = String(path[i + 1]).match(/^\[(\d+)\]$/)
      if (m) {
        return parseInt(m[1], 10)
      }
    }
  }
  return null
}

function _requestIndex(req, field, marker) {
  if (req[field] !== undefined && req[field] !== null) {
    var direct = parseInt(req[field], 10)
    if (!isNaN(direct)) {
      return direct
    }
  }
  return _pathIndexAfter(req, marker)
}

function _assetFromCached(base, req) {
  var item = {}
  for (var k in base) {
    item[k] = base[k]
  }
  item.request_id = req.id
  return item
}

function _captureStackChannels(req, index, outDir, assets, cache) {
  var materialIndex = _requestIndex(req, "material_index", "DataDocument.materials")
  var stackIndex = _requestIndex(req, "stack_index", "DataMaterial.stacks")
  for (var s = 0; s < index.stacks.length; ++s) {
    var stack = index.stacks[s]
    if (materialIndex !== null && stack.material_index !== materialIndex) {
      continue
    }
    if (stackIndex !== null && stack.stack_index !== stackIndex) {
      continue
    }
    for (var c = 0; c < stack.channels.length; ++c) {
      var channel = stack.channels[c]
      if (!_requestWantsChannel(req, channel)) {
        continue
      }
      var key = "stack|" + stack.material_index + "|" + stack.stack_index + "|" + channel
      if (cache[key] === undefined) {
        var name = _safe("stack_" + stack.material_index + "_" + stack.stack_index + "_" + channel + ".png")
        _save([stack.material, stack.stack, channel], outDir + name, _exportConfig("content", {
          path: [stack.material, stack.stack],
          name: channel
        }))
        cache[key] = {
          path: name,
          material: stack.material,
          stack: stack.stack,
          material_index: stack.material_index,
          stack_index: stack.stack_index,
          channel_index: c,
          channel: channel,
          kind: "full_stack_channel",
          mime: "image/png"
        }
      }
      assets.push(_assetFromCached(cache[key], req))
    }
  }
}

function _channelKey(channel) {
  return String(channel || "").toLowerCase().replace(/[^a-z0-9]/g, "")
}

function _requestWantsChannel(req, channel) {
  var capture = req.capture || {}
  var mask = capture.channel_mask
  if (mask === undefined || mask === null || mask === 0 || mask >= 9007199254740991) {
    return true
  }
  var key = _channelKey(channel)
  if (mask === 1) {
    return key === "basecolor" || key === "basecolour" || key === "diffuse"
  }
  var knownBits = {
    basecolor: 0,
    basecolour: 0,
    diffuse: 0,
    height: 1,
    roughness: 7,
    metallic: 13,
    metalness: 13,
    normal: 22,
    normalopengl: 22,
    normaldirectx: 22
  }
  var bit = knownBits[key]
  if (bit === undefined && key.indexOf("user") === 0) {
    var userIndex = Number(key.substring(4))
    if (userIndex >= 0 && userIndex < 8 && Math.floor(userIndex) === userIndex) {
      bit = 14 + userIndex
    }
  }
  if (bit === undefined) {
    return true
  }
  return Math.floor(mask / Math.pow(2, bit)) % 2 === 1
}

function _captureLayerChannels(req, uid, index, outDir, assets, cache) {
  var entry = index.byUid[String(uid)]
  for (var c = 0; c < entry.channels.length; ++c) {
    var channel = entry.channels[c]
    if (!_requestWantsChannel(req, channel)) {
      continue
    }
    var key = "layer|" + uid + "|" + channel
    if (cache[key] === undefined) {
      var chName = _safe("layer_" + uid + "_" + channel + ".png")
      _save([uid, channel], outDir + chName, _exportConfig("content", {
        path: [entry.material, entry.stack],
        name: channel
      }))
      cache[key] = {
        path: chName,
        material: entry.material,
        stack: entry.stack,
        material_index: entry.material_index,
        stack_index: entry.stack_index,
        channel_index: c,
        channel: channel,
        kind: "content",
        mime: "image/png"
      }
    }
    var asset = _assetFromCached(cache[key], req)
    asset.kind = req.scope === "group" ? "group" : "content"
    assets.push(asset)
  }
}

function _hasChannel(channels, wanted) {
  wanted = String(wanted).toLowerCase()
  for (var i = 0; i < channels.length; ++i) {
    if (String(channels[i]).toLowerCase() === wanted) {
      return true
    }
  }
  return false
}

function _ensureBlendingMask(entry, added) {
  if (added[entry.material]) {
    return
  }
  var stackPath = entry.stack && entry.stack !== entry.material
    ? [entry.material, entry.stack]
    : entry.material
  var channels = alg.mapexport.channelIdentifiers(stackPath)
  if (!_hasChannel(channels, "blendingmask")) {
    alg.texturesets.addChannel(entry.material, "blendingmask", "L8")
    added[entry.material] = true
  }
}

function capture(planPath, manifestPath) {
  var plan = _readJson(planPath)
  var index = _indexDocument()
  var outDir = _dir(manifestPath)
  var assets = []
  var warnings = []
  var requests = plan.requests || []
  var cache = {}
  var addedBlendingMasks = {}

  try {
    for (var i = 0; i < requests.length; ++i) {
      var req = requests[i]
      var cap = req.capture || {}
      var selector = cap.selector || []
      var uid = selector.length ? selector[0] : req.layer_uid

      try {
        if (req.kind === "mask" && uid !== null && uid !== undefined) {
          var maskEntry = index.byUid[String(uid)]
          if (!maskEntry) {
            throw new Error("mask layer uid " + uid + " was not found")
          }
          _ensureBlendingMask(maskEntry, addedBlendingMasks)
          var maskKey = "mask|" + uid
          if (cache[maskKey] === undefined) {
            var maskName = _safe("mask_" + uid + ".png")
            _save([uid, "mask"], outDir + maskName, _exportConfig("mask"))
            cache[maskKey] = {path: maskName, kind: "mask", mime: "image/png"}
          }
          assets.push(_assetFromCached(cache[maskKey], req))
          // UV-tile masks cannot be attached independently in the v8 graph.
          // Capture the same layer's content so the builder can wrap exact
          // per-tile content+mask proxies while the original graph stays in USPP.
          _captureLayerChannels(req, uid, index, outDir, assets, cache)
        } else if (req.scope === "full_stack_channel") {
          _captureStackChannels(req, index, outDir, assets, cache)
        } else if (uid !== null && uid !== undefined && index.byUid[String(uid)]) {
          _captureLayerChannels(req, uid, index, outDir, assets, cache)
        } else {
          warnings.push("request " + req.id + " had no capturable layer uid")
        }
      } catch (e) {
        warnings.push("failed to capture " + req.id + ": " + e.message)
      }
    }
  } finally {
    for (var material in addedBlendingMasks) {
      try {
        alg.texturesets.removeChannel(material, "blendingmask")
      } catch (e) {
        warnings.push("failed to remove temporary blending mask from " + material + ": " + e.message)
      }
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
