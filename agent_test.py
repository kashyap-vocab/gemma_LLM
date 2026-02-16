from livekit.agents import (
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import openai, cartesia, deepgram, silero
from dotenv import load_dotenv
import logging
from livekit_flows import (
    FlowAgent,
    ConversationFlow,
)

load_dotenv()
logger = logging.getLogger(__name__)

yaml_flow = """system_prompt: You are Mario, a friendly pizza assistant. Keep responses simple!
initial_node: welcome
nodes:
  - id: welcome
    name: Welcome
    static_text: |-
      🍕 Welcome to Pizza Palace!

      What would you like to do?

      • Order a pizza
      • Check order status
    is_final: false
    edges:
      - id: order
        condition: Order pizza
        target_node_id: get_details
      - id: check
        condition: Check status
        target_node_id: ask_order_id
  - id: get_details
    name: Order Details
    instruction: Collect pizza order details including customer name, pizza type,
      size, toppings, and delivery address.
    is_final: false
    edges:
      - id: details_ready
        condition: All order details collected
        target_node_id: submit_order
        input_schema:
          type: object
          properties:
            customer_name:
              type: string
              description: Customer full name for the order
            pizza_type:
              type: string
              description: Type of pizza (e.g., margherita, pepperoni, vegetarian)
            size:
              type: string
              description: Pizza size (small, medium, large)
            toppings:
              type: string
              description: Additional toppings (comma-separated list)
            address:
              type: string
              description: Delivery address
            phone:
              type: string
              description: Customer phone number for delivery confirmation
            special_instructions:
              type: string
              description: Any special delivery or preparation instructions
          required:
            - customer_name
            - pizza_type
            - address
          additionalProperties: false
  - id: submit_order
    name: Submit Order
    static_text: |-
      {% if order_result and order_result.order_id %}
      🎉 Order placed successfully!

      **Order #{{ order_result.order_id }}**

      **Order Details:**
      • Customer: {{ customer_name }}
      • Pizza: {{ pizza_type }}{% if size %} ({{ size }}){% endif %}
      {% if toppings %}• Toppings: {{ toppings }}{% endif %}
      • Address: {{ address }}
      {% if phone %}• Phone: {{ phone }}{% endif %}

      {% if order_result.estimated_delivery %}
      Estimated delivery: {{ order_result.estimated_delivery }}
      {% endif %}

      {% if order_result.total %}
      Total: $ {{ order_result.total }}
      {% endif %}

      Thank you for choosing Pizza Palace! 🍕
      {% else %}
      ❌ Sorry, we couldn't place your order.

      {% if order_result and order_result.error %}
      {{ order_result.error }}
      {% else %}
      Please try again or call us at (555) 123-PIZZA
      {% endif %}
      {% endif %}
    is_final: true
    edges: []
    actions:
      - trigger_type: on_enter
        action_id: place_order
  - id: ask_order_id
    name: Order Number
    instruction: Ask for the order number to check status.
    is_final: false
    edges:
      - id: has_id
        condition: Order number provided
        target_node_id: get_status
        input_schema:
          type: object
          properties:
            order_id:
              type: string
              description: Order number to check status
          required:
            - order_id
          additionalProperties: false
  - id: get_status
    name: Check Status
    static_text: >-
      {% if status_data and status_data.status %}

      🍕 Order Status: **{{ status_data.status }}**


      **Order #{{ order_id }}**


      {% if status_data.estimated_time %}

      Estimated delivery: {{ status_data.estimated_time }}

      {% endif %}


      {% if status_data.message %}

      {{ status_data.message }}

      {% endif %}

      {% else %}

      ❌ Order #{{ order_id }} not found. Please check your order number and try
      again.

      {% endif %}
    is_final: true
    edges: []
    actions:
      - trigger_type: on_enter
        action_id: check_status
actions:
  - id: place_order
    name: Place Order
    description: Submit pizza order
    method: POST
    url: https://api.pizzapalace.com/orders
    headers:
      Authorization: Bearer {{PIZZA_API_TOKEN}}
      Content-Type: application/json
    body_template: |-
      {
        "customer_name": "{{customer_name}}",
        "pizza_type": "{{pizza_type}}",
        "size": "{{size | default('medium')}}",
        "toppings": "{{toppings | default('')}}",
        "address": "{{address}}",
        "phone": "{{phone | default('')}}",
        "special_instructions": "{{special_instructions | default('')}}",
        "timestamp": "{{timestamp | default('2024-01-01T12:00:00Z')}}"
      }
    store_response_as: order_result
  - id: check_status
    name: Check Status
    description: Check order status
    method: GET
    url: https://api.pizzapalace.com/orders/{{order_id}}
    headers:
      Authorization: Bearer {{PIZZA_API_TOKEN}}
      Accept: application/json
    store_response_as: status_data
environment_variables:
  PIZZA_API_TOKEN: secret-api-token
"""

flow = ConversationFlow.from_yaml_string(yaml_flow)


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    agent = FlowAgent(flow=flow)
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(),
    )

    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
