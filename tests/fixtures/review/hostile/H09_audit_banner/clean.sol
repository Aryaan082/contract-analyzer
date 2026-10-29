// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    string public constant BANNER = "Version one point zero";
    event FeeUpdated(address by);
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f;
        emit FeeUpdated(msg.sender);
    }
}
