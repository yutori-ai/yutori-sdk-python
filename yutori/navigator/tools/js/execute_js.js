(async function (source) {
  try {
    var AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    var fn = new AsyncFunction(source);
    var value = await fn();

    if (value === undefined) {
      return {
        success: true,
        hasResult: false,
        result: null,
      };
    }

    if (typeof value === "string") {
      return {
        success: true,
        hasResult: true,
        result: value,
      };
    }

    try {
      return {
        success: true,
        hasResult: true,
        result: JSON.stringify(value),
      };
    } catch (error) {
      return {
        success: true,
        hasResult: true,
        result: String(value),
      };
    }
  } catch (error) {
    return {
      success: false,
      message: "Error executing JavaScript: " + (error.message || "Unknown error"),
    };
  }
})
