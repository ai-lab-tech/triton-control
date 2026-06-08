import http from "node:http";

const port = Number(process.env.MOCK_TRITON_PORT ?? 9000);

const sendJson = (res, status, payload) => {
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(payload));
};

const server = http.createServer((req, res) => {
  if (!req.url || !req.method) {
    res.statusCode = 400;
    res.end();
    return;
  }

  const path = req.url.split("?")[0];

  if (req.method === "GET" && path === "/v2") {
    sendJson(res, 200, {
      name: "mock-triton",
      version: "0.0.1",
      extensions: ["model_repository"],
    });
    return;
  }

  if (req.method === "GET" && path === "/v2/health/live") {
    res.statusCode = 200;
    res.end("OK");
    return;
  }

  if (req.method === "GET" && path === "/v2/health/ready") {
    res.statusCode = 200;
    res.end("OK");
    return;
  }

  if (req.method === "POST" && path === "/v2/repository/index") {
    sendJson(res, 200, [{ name: "resnet50", version: "1", state: "READY" }]);
    return;
  }

  if (req.method === "GET" && path === "/metrics") {
    res.statusCode = 200;
    res.setHeader("content-type", "text/plain; version=0.0.4");
    res.end(
      [
        "# HELP nv_cpu_utilization CPU utilization percentage",
        "# TYPE nv_cpu_utilization gauge",
        "nv_cpu_utilization 35",
        "# HELP nv_cpu_memory_used_bytes Used host RAM bytes",
        "# TYPE nv_cpu_memory_used_bytes gauge",
        "nv_cpu_memory_used_bytes 2147483648",
        "# HELP nv_cpu_memory_total_bytes Total host RAM bytes",
        "# TYPE nv_cpu_memory_total_bytes gauge",
        "nv_cpu_memory_total_bytes 4294967296",
        "# HELP nv_gpu_utilization GPU utilization percentage",
        "# TYPE nv_gpu_utilization gauge",
        "nv_gpu_utilization 12",
        "# TYPE nv_inference_request_success counter",
        'nv_inference_request_success{model="resnet50",version="1"} 100',
        "# TYPE nv_inference_request_duration_us counter",
        'nv_inference_request_duration_us{model="resnet50",version="1"} 1000000',
      ].join("\n"),
    );
    return;
  }

  if (req.method === "GET" && path === "/v2/models/stats") {
    sendJson(res, 200, {
      model_stats: [
        {
          name: "resnet50",
          version: "1",
          inference_count: 101,
          inference_stats: {
            success: { count: 101, ns: 1010000000 },
            queue: { count: 101, ns: 505000000 },
            compute_input: { count: 101, ns: 101000000 },
            compute_infer: { count: 101, ns: 202000000 },
            compute_output: { count: 101, ns: 101000000 },
          },
        },
      ],
    });
    return;
  }

  if (req.method === "POST" && path === "/v2/repository/models/resnet50/load") {
    sendJson(res, 200, {});
    return;
  }

  if (req.method === "POST" && path === "/v2/repository/models/resnet50/unload") {
    sendJson(res, 200, {});
    return;
  }

  if (req.method === "POST" && path === "/v2/models/resnet50/versions/1/infer") {
    sendJson(res, 200, {
      model_name: "resnet50",
      model_version: "1",
      outputs: [{ name: "OUTPUT0", shape: [1, 1], datatype: "FP32", data: [0.99] }],
    });
    return;
  }

  sendJson(res, 404, { detail: "Not found" });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Mock Triton server listening on http://127.0.0.1:${port}`);
});
