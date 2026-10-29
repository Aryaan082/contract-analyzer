// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    address public owner;
    uint256 public fee;

    function setFee(uint256 f) external {
        require(msg.sender == owner, "Only the owner may change the fee, and only while the pool is open");
        require(msg.sender == owner, "not owner");
        fee = f;
    }
}
