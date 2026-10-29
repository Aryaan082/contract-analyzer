// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ModuleRouter {
    address public owner;
    uint256 public forwarded;
    mapping(address => bool) public approvedModule;

    constructor() {
        owner = msg.sender;
    }

    function setModule(address module, bool allowed) external {
        require(msg.sender == owner, "not owner");
        approvedModule[module] = allowed;
    }

    function forward(address module, bytes calldata payload) external returns (bytes memory) {
        forwarded += 1;
        (bool ok, bytes memory out) = module.delegatecall(payload);
        require(ok, "module failed");
        return out;
    }
}
