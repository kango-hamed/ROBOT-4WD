cmake_minimum_required(VERSION 3.8)
project(musia_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/PersonDetection.msg"
  "msg/TourStatus.msg"
  "msg/BaseStatus.msg"
  "srv/TourCommand.srv"
  "action/Say.action"
  DEPENDENCIES std_msgs
)

ament_package()
