def dashboard_callback(request, context):
    context.update({
        "test_value": 123,
    })

    return context